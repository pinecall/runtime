"""What an org may dial, when and how often: shape, strangers, the callee's hours, the pace."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

import phonenumbers
from phonenumbers import timezone
from pydantic import BaseModel, ConfigDict, Field

from pinecall.domain.country_codes import CALLING_CODES, NEVER_DIALLED, SHORTEST_NATIONAL
from pinecall.domain.errors import DeclarationRefused, NotAllowed, QuotaExhausted
from pinecall.domain.names import parse_e164
from pinecall.domain.scope import Scope
from pinecall.log.inbox import ever_reached
from pinecall.postgres.pool import Pool
from pinecall.tenancy import consents, policy
from pinecall.tenancy.consents import Given
from pinecall.wire.rest.numbers import Consent

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


QUIET_HOURS = "quiet_hours"


TOO_OFTEN = "too_often"


DO_NOT_CALL = "do_not_call"


NO_CONSENT = "no_consent"


# The Telemarketing Sales Rule (16 CFR 310.4(c)): not before 8 a.m. or after 9 p.m. at the called
# person's location. It binds a call to the US; Canada's rules sit inside the same hours.
NORTH_AMERICA = "+1"


US_HOURS = (8, 21)


# Florida's "mini-TCPA" caps three calls in 24 hours to one person on one subject: the default for
# a +1 number, which the org may change. Elsewhere nothing, unless the org sets one.
US_PER_NUMBER_DAY = 3


NO_ZONE = "Etc/Unknown"


# How many of a number's zones a refusal names before it says "…".
ZONES_SAID = 3


OUT_OF_HOURS = (
    "{number} is outside the hours it may be rung ({window}) in {zones} ({guard}); "
    "the sandbox and your own phone are not held to them"
)


UNPLACED = "{number} has no zone this box can place it in, so its hours cannot be kept ({guard})"


A_NUMBERS_DAY = "{number} was rung {used} times in the last day, of {limit} ({guard})"


ON_THE_LIST = (
    "{number} asked not to be called: it is on this org's do-not-call list ({guard}); only a "
    "consent recorded at POST /v1/org/consents lifts it, never one sent with a dial"
)


NO_CONSENT_ON_FILE = (
    "{number} has no consent on file ({guard}): an AI-voice call needs the person's prior express "
    "consent. Send it as `consent` with the dial, or record it at POST /v1/org/consents"
)


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


# A number's own count is of the calls placed to it, not the refusals: a refused dial rang nobody.
PACED = """
WITH counted AS (
    SELECT count(*) FILTER (WHERE at > now() - interval '1 minute') AS minute,
           count(*) AS day,
           count(*) FILTER (WHERE dialled = %(dialled)s AND refused IS NULL) AS number_day
    FROM dials WHERE org = %(org)s AND at > now() - interval '1 day'
), judged AS (
    SELECT minute, day, number_day,
           CASE WHEN minute >= %(per_minute)s THEN 'too_fast'
                WHEN day >= %(per_day)s THEN 'too_many'
                WHEN %(per_number_day)s::int IS NOT NULL AND number_day >= %(per_number_day)s::int
                    THEN 'too_often' END AS refused
    FROM counted
)
INSERT INTO dials (org, env, agent, call, dialled, shown, asked_by, refused)
SELECT %(org)s, %(env)s, %(agent)s, CASE WHEN judged.refused IS NULL THEN %(call)s END,
       %(dialled)s, %(shown)s, %(asked_by)s, judged.refused
FROM judged
RETURNING refused, (SELECT minute FROM judged) AS minute, (SELECT day FROM judged) AS day,
          (SELECT number_day FROM judged) AS number_day
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
    """One dial asked for: whose, to whom, shown as what, by whom, as which call, and when."""

    scope: Scope
    agent: str
    to: str
    shown: str | None
    asked_by: str
    call: str
    at: datetime
    # The number is a phone the person who asked verified as their own: testing, not calling out.
    own_phone: bool = False
    # The consent the call runs on, as the dial carried it; written down before anything rings.
    consent: Consent | None = None


@dataclass(frozen=True)
class Rules:
    """What the called number's destination and the org's policy hold a dial to."""

    hours: tuple[int, int] | None
    per_number_day: int | None
    # Held to the do-not-call list at all: not in the sandbox, not the asker's own phone.
    listed: bool = False
    consent_required: bool = False


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
    """Every guard on a call placed cold: shape, a stranger, hours, the pace; one ledger row."""
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
    rules = await _rules_for(pool, dial, destination)
    await _consented(pool, dial, destination, rules)
    if rules.hours is not None:
        await _in_hours(pool, dial, destination, rules.hours)
    await _paced(pool, dial, guards, rules.per_number_day)
    return guards


def zones_of(number: str) -> tuple[str, ...]:
    """Every time zone the number could be in; empty when none is known."""
    zones = timezone.time_zones_for_number(phonenumbers.parse(number))
    return tuple(zone for zone in zones if zone != NO_ZONE)


def within(zones: tuple[str, ...], at: datetime, hours: tuple[int, int]) -> bool:
    """Whether it is inside the hours in every zone: a number in two zones is held to both."""
    start, end = hours
    return all(start <= at.astimezone(ZoneInfo(zone)).hour < end for zone in zones)


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
    await _paced(pool, dial, await guards_of(pool, dial.scope.org), None)


async def _shaped(pool: Pool, dial: Dial) -> str:
    try:
        return destination_of(dial.to)
    except DeclarationRefused as malformed:
        await _refused(pool, dial, SHAPE)
        raise DeclarationRefused(f"{malformed} ({SHAPE})") from None


async def _refused(pool: Pool, dial: Dial, guard: str) -> None:
    async with pool.connection() as connection:
        await connection.execute(REFUSED, {**_ledger(dial), "refused": guard})


# The rules bind a call out to a person. The sandbox is where an org tries its agent, and a
# person's own verified phone is them testing: neither is held to a caller's hours or count.
async def _rules_for(pool: Pool, dial: Dial, destination: str) -> Rules:
    """The list, consent, hours and daily count a dial is held to, by destination and policy."""
    if dial.scope.env == "sandbox" or dial.own_phone:
        return Rules(hours=None, per_number_day=None)
    kept = (await policy.policy_of(pool, dial.scope.org)).policy
    window = (
        None if kept.calling_hours is None else (kept.calling_hours.from_, kept.calling_hours.until)
    )
    if destination.startswith(NORTH_AMERICA):
        floor, top = US_HOURS
        start, end = window if window is not None else US_HOURS
        return Rules(
            hours=(max(floor, start), min(top, end)),
            per_number_day=kept.per_number_day or US_PER_NUMBER_DAY,
            listed=True,
            consent_required=True,
        )
    return Rules(
        hours=window,
        per_number_day=kept.per_number_day,
        listed=True,
        consent_required=kept.consent_everywhere,
    )


# An opt-out outranks the consent a dial carries: an app may not lift the list by sending one. A
# dial the list does not bind (the sandbox, a developer's own phone) rings past it, and writes no
# consent over it either.
async def _consented(pool: Pool, dial: Dial, destination: str, rules: Rules) -> None:
    current = await consents.standing_of(pool, dial.scope, destination)
    if current == "opted_out" and not rules.listed:
        return
    if current == "opted_out":
        await _refused(pool, dial, DO_NOT_CALL)
        raise NotAllowed(ON_THE_LIST.format(number=destination, guard=DO_NOT_CALL))
    if dial.consent is not None:
        given = Given(
            kind=dial.consent.kind,
            source=dial.consent.source,
            given_by=dial.asked_by,
            text=dial.consent.text,
            evidence=dial.consent.evidence,
            call=dial.call,
        )
        await consents.give(pool, dial.scope, destination, given)
        return
    if rules.consent_required and current != "consented":
        await _refused(pool, dial, NO_CONSENT)
        raise NotAllowed(NO_CONSENT_ON_FILE.format(number=destination, guard=NO_CONSENT))


async def _in_hours(pool: Pool, dial: Dial, destination: str, hours: tuple[int, int]) -> None:
    zones = zones_of(destination)
    if not zones:
        await _refused(pool, dial, QUIET_HOURS)
        raise NotAllowed(UNPLACED.format(number=destination, guard=QUIET_HOURS))
    if not within(zones, dial.at, hours):
        await _refused(pool, dial, QUIET_HOURS)
        window = f"{hours[0]}:00 to {hours[1]}:00"
        named = ", ".join(zones[:ZONES_SAID]) + ("…" if len(zones) > ZONES_SAID else "")
        raise NotAllowed(
            OUT_OF_HOURS.format(number=destination, window=window, zones=named, guard=QUIET_HOURS)
        )


async def _paced(pool: Pool, dial: Dial, guards: Guards, per_number_day: int | None) -> None:
    params = {
        **_ledger(dial),
        "call": dial.call,
        "per_minute": guards.per_minute,
        "per_day": guards.per_day,
        "per_number_day": per_number_day,
    }
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(LOCKED, {"org": dial.scope.org})
        row = await (await connection.execute(PACED, params)).fetchone()
    if row is None or row["refused"] is None:
        return
    if row["refused"] == TOO_FAST:
        sentence = A_BURST.format(used=row["minute"], limit=guards.per_minute, guard=TOO_FAST)
    elif row["refused"] == TOO_OFTEN:
        sentence = A_NUMBERS_DAY.format(
            number=dial.to, used=row["number_day"], limit=per_number_day, guard=TOO_OFTEN
        )
    else:
        sentence = A_DAYS_WORTH.format(used=row["day"], limit=guards.per_day, guard=TOO_MANY)
    raise QuotaExhausted(sentence)


def _ledger(dial: Dial) -> dict[str, str | None]:
    return {
        "org": dial.scope.org,
        "env": dial.scope.env,
        "agent": dial.agent,
        "dialled": dial.to.strip(),
        "shown": dial.shown,
        "asked_by": dial.asked_by,
    }
