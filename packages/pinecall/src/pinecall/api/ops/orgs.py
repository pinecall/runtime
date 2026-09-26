"""The operator's doors onto the tenants: orgs, each one's keys, and the quotas set on each."""

from __future__ import annotations

from fastapi import HTTPException
from starlette.status import HTTP_204_NO_CONTENT

from pinecall.api.accounts.api_keys import KeyIssued, KeyRevoked, in_this_world, wire_key_issued
from pinecall.api.deps import (
    KeysDep,
    KnowledgeDep,
    MembersDep,
    MemoryDep,
    OrgsDep,
    RegistryDep,
    RoutesDep,
    SettingsDep,
    StoreDep,
    require_org,
)
from pinecall.api.scope.operator_key import operators_router
from pinecall.api.telephony.deps import DialPoliciesDep
from pinecall.auth.keys import ListedKey
from pinecall.providers.lent_keys import NotLent, parse_lending
from pinecall.types import (
    KEY_SCOPES,
    PRODUCTION,
    SANDBOX,
    DeclarationRefused,
    DialPolicy,
    Org,
    Quotas,
    key_scopes,
    parse_slug,
)
from pinecall_protocol import WireModel
from pinecall_protocol.rest import DialGuards

# Every /v1/ops door takes the operator key and nothing else, checked before the endpoint runs.
# The same gate the routes doors take, from api/deps.py: there is one, and this is it.
operator = operators_router()

# The slug is the operator's word for the tenant and two tenants cannot share one.
SLUG_TAKEN = "an org already answers to the slug {slug}"

# An org that still holds a live key or a route is an org somebody is still using: the keys are
# revoked and the numbers moved first, in the operator's own terminal, and only then does the
# row go. Nothing cascades into a tenant's doors.
STILL_IN_USE = "org {org} still has {what}: revoke its keys and remove its routes first"

# Nothing answers to that fingerprint, or it was already revoked. 404 and not 200: `keys revoke`
# on a typo must never read as done.
NO_SUCH_KEY = "no live key with fingerprint {fingerprint}"


class WantedOrg(WireModel):
    """What `orgs add` sends: the slug people will type, and a name when it differs."""

    slug: str
    name: str | None = None


class WantedKey(WireModel):
    """What `keys issue` sends: what the key is for, where it opens, what it may do, whose it is."""

    label: str | None = None
    # The instance's world when left out, and refused when it names the other: an instance IS one
    # world, and a key minted here for the other would open nothing there — the sandbox's worker
    # key came out production's at the cutover and every heartbeat was refused (2026-09-25).
    env: str | None = None
    # Every scope when left out, which is what an org's own machine key holds. A person's key is
    # issued with the scopes their role presets.
    scopes: list[str] | None = None
    subject: str | None = None
    name: str | None = None


class WantedDialling(WireModel):
    """What `orgs dialling` sends: the whole set. A guard left out is the code's own default."""

    dial_anywhere: bool | None = None
    per_minute: int | None = None
    per_day: int | None = None
    max_duration_s: int | None = None


class WantedQuotas(WireModel):
    """What `orgs quota` sends: the whole set. A limit left out is no limit."""

    minutes: int | None = None
    messages: int | None = None
    agents: int | None = None
    concurrent_calls: int | None = None
    memory_facts: int | None = None
    knowledge_chunks: int | None = None
    numbers: int | None = None
    seats: int | None = None
    llm_tokens: int | None = None
    budget_eur: int | None = None
    # Which of the box's keys the org may run on: absent or null lends all, [] lends nothing,
    # else vendors and `vendor/model` entries (providers/lent_keys.py).
    lends: list[str] | None = None


# A set has no order and JSON has no set: the lending is answered sorted, so the same row always
# reads the same and a diff between two answers is a real one.
class OrgQuotas(WireModel):
    """The quotas row as the operator reads it back: `null` is no limit, `lends` is sorted."""

    minutes: int | None
    messages: int | None
    agents: int | None
    concurrent_calls: int | None
    memory_facts: int | None
    knowledge_chunks: int | None
    numbers: int | None
    seats: int | None
    llm_tokens: int | None
    budget_eur: int | None
    lends: list[str] | None


class OrgHolding(WireModel):
    """What the org holds right now, against the quotas that are stocks."""

    memory_facts: int
    knowledge_chunks: int
    numbers: int
    seats: int


class OrgStanding(WireModel):
    """One org whole: the row, its quotas, its dial guards, and what it holds."""

    id: str
    slug: str
    name: str
    quotas: OrgQuotas
    dialling: DialGuards
    holding: OrgHolding


class AgentMoved(WireModel):
    """`orgs move` done: the agent, the org it landed in, how many logs and which doors went."""

    agent: str
    org: str
    logs: int
    numbers: list[str]
    stayed: list[str]


# ── the orgs ────────────────────────────────────────────────────────────────────


@operator.get("/orgs")
async def list_orgs(orgs: OrgsDep) -> list[Org]:
    """Every org, oldest first: the default one is always the first line."""
    return list(await orgs.listed())


@operator.post("/orgs")
async def add(said: WantedOrg, orgs: OrgsDep) -> Org:
    """A new tenant. The id is minted here and is what every row of theirs will name."""
    slug = parse_slug(said.slug)
    org = await orgs.create(slug, said.name or slug)
    if org is None:
        raise HTTPException(409, SLUG_TAKEN.format(slug=slug))
    return org


# `holding` is what the three STOCK quotas are measured against, and it is answered here rather than
# in /v1/ops/usage because that door is a cursor-paged fold of the log: every row there is an
# event that happened at a position, and a count of what stands right now is not an event. Both
# counts are one indexed query over tables that already exist — never a counter column, and never
# a table. On a gateway with no Postgres there is nowhere for the first two to be, so both are zero.
@operator.get("/orgs/{named}")
async def one(
    named: str,
    orgs: OrgsDep,
    memory: MemoryDep,
    knowledge: KnowledgeDep,
    table: RoutesDep,
    members: MembersDep,
    policies: DialPoliciesDep,
) -> OrgStanding:
    """One org: its quotas, its dial guards, and what it holds against the ones that are stocks."""
    org = await require_org(named, orgs)
    return OrgStanding(
        id=org.id,
        slug=org.slug,
        name=org.name,
        quotas=_the_quotas_read_back(await orgs.quotas_of(org.id)),
        dialling=_the_guards(await policies.of(org.id)),
        holding=OrgHolding(
            memory_facts=0 if memory is None else await memory.kept(org.id),
            knowledge_chunks=0 if knowledge is None else await knowledge.kept(org.id),
            numbers=await table.managed_by(org.id),
            seats=await members.seated(org.id),
        ),
    )


@operator.delete("/orgs/{named}", status_code=HTTP_204_NO_CONTENT)
async def remove(named: str, orgs: OrgsDep, keys: KeysDep, table: RoutesDep) -> None:
    """Forget the org. Refused while a live key or a route still names it."""
    org = await require_org(named, orgs)
    if any(key.revoked_at is None for key in await keys.listed(org.id)):
        raise HTTPException(409, STILL_IN_USE.format(org=org.slug, what="live keys"))
    for env in (PRODUCTION, SANDBOX):
        if await table.of_org(org.id, env):
            raise HTTPException(409, STILL_IN_USE.format(org=org.slug, what="routes"))
    await orgs.remove(org.id)


# ── an agent that ended up in the wrong org ─────────────────────────────────────

# A slug is one org's for as long as its log exists, and until this door there was no way back:
# an agent registered from a terminal pointed at the wrong key belonged to that org for good, with
# every call it had ever taken. It happens on a first install more than anywhere else — a box's
# own worker and operator keys are issued into `default`, so the first agent anybody runs there
# lands in `default` too, and the org the tenant meant to use is left empty beside it.
#
# The box operator's, and nobody else's: an org that could pull a slug would be an org that could
# take another's agent, which is the very rule this undoes.
NOT_HELD = "agent {slug} is held right now: stop it, move it, and start it again"
NO_SUCH_AGENT = "no agent named {slug} has ever written a log here"


class WantedMove(WireModel):
    """Which agent is being moved. The org it lands in is the one in the path."""

    agent: str


@operator.put("/orgs/{named}/agents")
async def move(
    named: str,
    said: WantedMove,
    orgs: OrgsDep,
    store: StoreDep,
    registry: RegistryDep,
    table: RoutesDep,
) -> AgentMoved:
    """This agent — its log, every call of it, and its doors — into this org. `orgs move`."""
    org = await require_org(named, orgs)
    if registry.held_anywhere(said.agent):
        raise HTTPException(409, NOT_HELD.format(slug=said.agent))
    moved = await store.moved(said.agent, org.id)
    if moved == 0:
        raise HTTPException(404, NO_SUCH_AGENT.format(slug=said.agent))
    # The doors go with it. Left behind, the number kept answering for an org that no longer holds
    # the slug, which is a number that reaches nobody — and nothing said so until somebody called.
    doors = await table.moved(said.agent, org.id)
    return AgentMoved(
        agent=said.agent,
        org=org.slug,
        logs=moved,
        numbers=list(doors.numbers),
        stayed=list(doors.stayed),
    )


# ── its quotas ──────────────────────────────────────────────────────────────────


@operator.put("/orgs/{named}/quotas")
async def set_quotas(named: str, said: WantedQuotas, orgs: OrgsDep) -> OrgQuotas:
    """Replace the org's limits, whole. They bite the next call and the next register."""
    org = await require_org(named, orgs)
    try:
        quotas = Quotas(
            minutes=said.minutes,
            messages=said.messages,
            agents=said.agents,
            concurrent_calls=said.concurrent_calls,
            memory_facts=said.memory_facts,
            knowledge_chunks=said.knowledge_chunks,
            numbers=said.numbers,
            seats=said.seats,
            llm_tokens=said.llm_tokens,
            budget_eur=said.budget_eur,
            lends=None if said.lends is None else parse_lending(said.lends),
        )
    except (DeclarationRefused, NotLent) as refused:
        raise HTTPException(400, str(refused)) from refused
    await orgs.set_quotas(org.id, quotas)
    return _the_quotas_read_back(quotas)


def _the_quotas_read_back(quotas: Quotas) -> OrgQuotas:
    """The row as the operator reads it back, the lending sorted."""
    return OrgQuotas(
        minutes=quotas.minutes,
        messages=quotas.messages,
        agents=quotas.agents,
        concurrent_calls=quotas.concurrent_calls,
        memory_facts=quotas.memory_facts,
        knowledge_chunks=quotas.knowledge_chunks,
        numbers=quotas.numbers,
        seats=quotas.seats,
        llm_tokens=quotas.llm_tokens,
        budget_eur=quotas.budget_eur,
        lends=None if quotas.lends is None else sorted(quotas.lends),
    )


# ── what it may dial ────────────────────────────────────────────────────────────


# Operator-only, and deliberately not beside `PUT /v1/org/judging`, which a tenant turns for
# itself: an org that could lift its own dialling fence has none. Replaced whole, as the quotas
# are, so a guard left out of the body goes back to the code's default rather than staying put.
@operator.put("/orgs/{named}/dialling")
async def set_dialling(
    named: str, said: WantedDialling, orgs: OrgsDep, policies: DialPoliciesDep
) -> DialGuards:
    """Replace the org's outbound guards, whole. They bite the next dial."""
    org = await require_org(named, orgs)
    standing = DialPolicy()
    policy = DialPolicy(
        dial_anywhere=standing.dial_anywhere if said.dial_anywhere is None else said.dial_anywhere,
        per_minute=standing.per_minute if said.per_minute is None else said.per_minute,
        per_day=standing.per_day if said.per_day is None else said.per_day,
        max_duration_s=standing.max_duration_s
        if said.max_duration_s is None
        else said.max_duration_s,
    )
    await policies.put(org.id, policy)
    return _the_guards(policy)


def _the_guards(policy: DialPolicy) -> DialGuards:
    """The org's dial guards as the wire spells them, which is field for field the policy's."""
    return DialGuards(
        dial_anywhere=policy.dial_anywhere,
        per_minute=policy.per_minute,
        per_day=policy.per_day,
        max_duration_s=policy.max_duration_s,
    )


# ── its keys ────────────────────────────────────────────────────────────────────


# The ONE response in the whole runtime that carries a key in the clear. It is built here, read
# once by the terminal that asked for it, and stored by nobody: docs/decisions/keys.md.
@operator.post("/orgs/{named}/keys")
async def issue(
    named: str, said: WantedKey, orgs: OrgsDep, keys: KeysDep, settings: SettingsDep
) -> KeyIssued:
    """Mint a key for the org, in this instance's world, and answer with it, the once. The table
    keeps its sha256."""
    org = await require_org(named, orgs)
    env = in_this_world(said.env, settings)
    scopes = KEY_SCOPES if said.scopes is None else key_scopes(said.scopes)
    issued = await keys.issue(
        org=org.id,
        label=said.label,
        env=env,
        scopes=scopes,
        subject=said.subject,
        name=said.name,
    )
    return wire_key_issued(issued)


@operator.get("/orgs/{named}/keys")
async def keys_listed(named: str, orgs: OrgsDep, keys: KeysDep) -> list[ListedKey]:
    """Every key of the org, oldest first, revoked ones included and named as revoked."""
    org = await require_org(named, orgs)
    return list(await keys.listed(org.id))


# A POST and not a DELETE, because nothing is deleted: the row stays and grows a timestamp, so the
# log entries that name this key stay readable. The verb on the wire says which of the two it is.
# It names no org: a fingerprint already names one row in the table.
@operator.post("/keys/{fingerprint}/revoke")
async def revoke(fingerprint: str, keys: KeysDep) -> KeyRevoked:
    """Stop honouring one key from the next request. Its row, and its history, stay."""
    if not await keys.revoke(fingerprint):
        raise HTTPException(404, NO_SUCH_KEY.format(fingerprint=fingerprint))
    return KeyRevoked(fingerprint=fingerprint, revoked=True)
