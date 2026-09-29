"""What an org may dial and how often: the destination's shape, strangers, the pace, the ledger."""

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from pinecall.domain.country_codes import CALLING_CODES, NEVER_DIALLED, SHORTEST_NATIONAL
from pinecall.domain.errors import DeclarationRefused, NotAllowed, QuotaExhausted
from pinecall.domain.names import parse_e164
from pinecall.domain.scope import Scope
from pinecall.log.queries import ever_reached
from pinecall.postgres.pool import Pool

NOT_A_COUNTRY = "{number} starts with no country calling code E.164 assigns"


A_BILL = "{number} is +{code}, a satellite or global-service range this box never dials"


TOO_SHORT = "{number} is shorter than a number anybody calls from: {digits} digits after +{code}"


# A call-back box, not a call centre: an operator raises them per org.
DIALS_A_MINUTE = 6


DIALS_A_DAY = 200


LONGEST_CALL_S = 600


# The guard that refused, written in the ledger and said in the sentence.
SHAPE = "shape"


STRANGER = "stranger"


TOO_FAST = "too_fast"


TOO_MANY = "too_many"


NOT_ONE_OF_OURS = (
    "{number} never called or wrote to this org in the {env}: a call back goes back to somebody "
    "({guard}); an operator lifts it with dial_anywhere"
)


A_BURST = "the org dialled {used} numbers in the last minute of its {limit} ({guard})"


A_DAYS_WORTH = "the org dialled {used} numbers today of its {limit} ({guard})"


POLICY = (
    "SELECT dial_anywhere, per_minute, per_day, max_duration_s FROM dial_policy WHERE org = %(org)s"
)


PUT_POLICY = """
INSERT INTO dial_policy (org, dial_anywhere, per_minute, per_day, max_duration_s)
VALUES (%(org)s, %(dial_anywhere)s, %(per_minute)s, %(per_day)s, %(max_duration_s)s)
ON CONFLICT (org) DO UPDATE SET dial_anywhere = excluded.dial_anywhere,
    per_minute = excluded.per_minute, per_day = excluded.per_day,
    max_duration_s = excluded.max_duration_s, set_at = now()
"""


# Two dials of one org cannot both take the last slot: the lock holds until the row is written.
LOCKED = "SELECT pg_advisory_xact_lock(hashtext('dials:' || %(org)s))"


PACED = """
WITH counted AS (
    SELECT count(*) FILTER (WHERE at > now() - interval '1 minute') AS minute,
           count(*) AS day
    FROM dials WHERE org = %(org)s AND at > now() - interval '1 day'
), judged AS (
    SELECT minute, day, CASE WHEN minute >= %(per_minute)s THEN 'too_fast'
                             WHEN day >= %(per_day)s THEN 'too_many' END AS refused
    FROM counted
)
INSERT INTO dials (org, env, agent, call, dialled, shown, asked_by, refused)
SELECT %(org)s, %(env)s, %(agent)s, CASE WHEN judged.refused IS NULL THEN %(call)s END,
       %(dialled)s, %(shown)s, %(asked_by)s, judged.refused
FROM judged
RETURNING refused, (SELECT minute FROM judged) AS minute, (SELECT day FROM judged) AS day
"""


REFUSED = """
INSERT INTO dials (org, env, agent, dialled, shown, asked_by, refused)
VALUES (%(org)s, %(env)s, %(agent)s, %(dialled)s, %(shown)s, %(asked_by)s, %(refused)s)
"""


# The first leg was judged when it was placed: its ledger row names the call.
PLACED = """
SELECT 1 FROM dials WHERE org = %(org)s AND call = %(call)s AND dialled = %(dialled)s
  AND refused IS NULL
"""


class Guards(BaseModel):
    """The org's dial guards: what it may dial and how often."""

    model_config = ConfigDict(frozen=True)

    dial_anywhere: bool = False
    per_minute: Annotated[int, Field(ge=0)] = DIALS_A_MINUTE
    per_day: Annotated[int, Field(ge=0)] = DIALS_A_DAY
    max_duration_s: Annotated[int, Field(ge=0)] = LONGEST_CALL_S


@dataclass(frozen=True)
class Dial:
    """One dial asked for: whose, to whom, shown as what, by whom, as which call."""

    scope: Scope
    agent: str
    to: str
    shown: str | None
    asked_by: str
    call: str


def destination_of(number: str) -> str:
    """The number a dial may reach, or DeclarationRefused saying why not."""
    e164 = parse_e164(number)
    digits = e164.removeprefix("+")
    code = next((digits[:length] for length in (3, 2, 1) if digits[:length] in CALLING_CODES), None)
    if code is None:
        raise DeclarationRefused(NOT_A_COUNTRY.format(number=e164))
    if code in NEVER_DIALLED:
        raise DeclarationRefused(A_BILL.format(number=e164, code=code))
    national = digits[len(code) :]
    if len(national) < SHORTEST_NATIONAL:
        raise DeclarationRefused(TOO_SHORT.format(number=e164, digits=len(national), code=code))
    return e164


async def guards_of(pool: Pool, org: str) -> Guards:
    """The org's guards; a column nobody set is the default."""
    async with pool.connection() as connection:
        row = await (await connection.execute(POLICY, {"org": org})).fetchone()
    if row is None:
        return Guards()
    return Guards.model_validate({name: value for name, value in row.items() if value is not None})


async def put_guards(pool: Pool, org: str, guards: Guards) -> None:
    """Replace the org's guards whole."""
    async with pool.connection() as connection:
        await connection.execute(PUT_POLICY, {"org": org, **guards.model_dump()})


async def guard_dial(pool: Pool, dial: Dial) -> Guards:
    """Every guard on a call placed cold: shape, a stranger, the pace; one ledger row whatever."""
    destination = await _shaped(pool, dial)
    guards = await guards_of(pool, dial.scope.org)
    reached = guards.dial_anywhere or await ever_reached(
        pool, dial.scope.org, dial.scope.env, destination
    )
    if not reached:
        await _refused(pool, dial, STRANGER)
        raise NotAllowed(
            NOT_ONE_OF_OURS.format(number=destination, env=dial.scope.env, guard=STRANGER)
        )
    await _paced(pool, dial, guards)
    return guards


# A transfer target need not have called: the stranger fence stays off, the pace caps a loop.
async def guard_second_leg(pool: Pool, dial: Dial) -> None:
    """The shape and the pace on a leg dialled into a live call; the call's own first leg passes."""
    async with pool.connection() as connection:
        placed = await (
            await connection.execute(
                PLACED, {"org": dial.scope.org, "call": dial.call, "dialled": dial.to}
            )
        ).fetchone()
    if placed is not None:
        return
    await _shaped(pool, dial)
    await _paced(pool, dial, await guards_of(pool, dial.scope.org))


async def _shaped(pool: Pool, dial: Dial) -> str:
    try:
        return destination_of(dial.to)
    except DeclarationRefused as malformed:
        await _refused(pool, dial, SHAPE)
        raise DeclarationRefused(f"{malformed} ({SHAPE})") from None


async def _refused(pool: Pool, dial: Dial, guard: str) -> None:
    async with pool.connection() as connection:
        await connection.execute(REFUSED, {**_ledger(dial), "refused": guard})


async def _paced(pool: Pool, dial: Dial, guards: Guards) -> None:
    params = {
        **_ledger(dial),
        "call": dial.call,
        "per_minute": guards.per_minute,
        "per_day": guards.per_day,
    }
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(LOCKED, {"org": dial.scope.org})
        row = await (await connection.execute(PACED, params)).fetchone()
    if row is None or row["refused"] is None:
        return
    if row["refused"] == TOO_FAST:
        sentence = A_BURST.format(used=row["minute"], limit=guards.per_minute, guard=TOO_FAST)
    else:
        sentence = A_DAYS_WORTH.format(used=row["day"], limit=guards.per_day, guard=TOO_MANY)
    raise QuotaExhausted(sentence)


def _ledger(dial: Dial) -> dict[str, str | None]:
    return {
        "org": dial.scope.org,
        "env": dial.scope.env,
        "agent": dial.agent,
        "dialled": dial.to,
        "shown": dial.shown,
        "asked_by": dial.asked_by,
    }
