"""The operator's doors onto the tenants: orgs, each one's keys, and the quotas set on each."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import TypeAdapter

from pinecall.api._deps import (
    KeysDep,
    KnowledgeDep,
    MembersDep,
    MemoryDep,
    OrgsDep,
    RoutesDep,
    SettingsDep,
    StoreDep,
    an_org,
)
from pinecall.api._operator import an_operator
from pinecall.api._placing import DialPoliciesDep
from pinecall.api.agents.registry import RegistryDep
from pinecall.api.keys import in_this_world
from pinecall.auth.keys import ListedKey
from pinecall.providers.lending import NotLent, a_lending
from pinecall.types import (
    KEY_SCOPES,
    PRODUCTION,
    SANDBOX,
    DeclarationRefused,
    DialPolicy,
    Org,
    Quotas,
    a_slug,
    key_scopes,
)
from pinecall_protocol import WireModel

# Every /v1/ops door takes the operator key and nothing else, checked before the endpoint runs.
# The same gate the routes doors take, from api/_deps.py: there is one, and this is it.
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])

ORGS: TypeAdapter[tuple[Org, ...]] = TypeAdapter(tuple[Org, ...])
LISTED: TypeAdapter[tuple[ListedKey, ...]] = TypeAdapter(tuple[ListedKey, ...])
QUOTAS: TypeAdapter[Quotas] = TypeAdapter(Quotas)
DIALLING: TypeAdapter[DialPolicy] = TypeAdapter(DialPolicy)

# A removed org has nothing to say back.
NO_BODY = 204

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
    budget_eur: int | None = None
    # Which of the box's keys the org may run on: absent or null lends all, [] lends nothing,
    # else vendors and `vendor/model` entries (providers/lending.py).
    lends: list[str] | None = None


# ── the orgs ────────────────────────────────────────────────────────────────────


@operator.get("/orgs")
async def listed(orgs: OrgsDep) -> list[dict[str, Any]]:
    """Every org, oldest first: the default one is always the first line."""
    return list(ORGS.dump_python(await orgs.listed(), mode="json"))


@operator.post("/orgs")
async def add(said: WantedOrg, orgs: OrgsDep) -> dict[str, Any]:
    """A new tenant. The id is minted here and is what every row of theirs will name."""
    try:
        slug = a_slug(said.slug)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    org = await orgs.create(slug, said.name or slug)
    if org is None:
        raise HTTPException(409, SLUG_TAKEN.format(slug=slug))
    return _as_json(org)


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
) -> dict[str, Any]:
    """One org: its quotas, its dial guards, and what it holds against the ones that are stocks."""
    org = await an_org(named, orgs)
    return {
        **_as_json(org),
        "quotas": quotas_as_json(await orgs.quotas_of(org.id)),
        "dialling": DIALLING.dump_python(await policies.of(org.id)),
        "holding": {
            "memory_facts": 0 if memory is None else await memory.kept(org.id),
            "knowledge_chunks": 0 if knowledge is None else await knowledge.kept(org.id),
            "numbers": await table.managed_by(org.id),
            "seats": await members.seated(org.id),
        },
    }


@operator.delete("/orgs/{named}", status_code=NO_BODY)
async def remove(named: str, orgs: OrgsDep, keys: KeysDep, table: RoutesDep) -> None:
    """Forget the org. Refused while a live key or a route still names it."""
    org = await an_org(named, orgs)
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
) -> dict[str, Any]:
    """This agent — its log, every call of it, and its doors — into this org. `orgs move`."""
    org = await an_org(named, orgs)
    if registry.held_anywhere(said.agent):
        raise HTTPException(409, NOT_HELD.format(slug=said.agent))
    moved = await store.moved(said.agent, org.id)
    if moved == 0:
        raise HTTPException(404, NO_SUCH_AGENT.format(slug=said.agent))
    # The doors go with it. Left behind, the number kept answering for an org that no longer holds
    # the slug, which is a number that reaches nobody — and nothing said so until somebody called.
    doors = await table.moved(said.agent, org.id)
    return {
        "agent": said.agent,
        "org": org.slug,
        "logs": moved,
        "numbers": list(doors.numbers),
        "stayed": list(doors.stayed),
    }


# ── its quotas ──────────────────────────────────────────────────────────────────


@operator.put("/orgs/{named}/quotas")
async def set_quotas(named: str, said: WantedQuotas, orgs: OrgsDep) -> dict[str, Any]:
    """Replace the org's limits, whole. They bite the next call and the next register."""
    org = await an_org(named, orgs)
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
            budget_eur=said.budget_eur,
            lends=None if said.lends is None else a_lending(said.lends),
        )
    except (DeclarationRefused, NotLent) as refused:
        raise HTTPException(400, str(refused)) from refused
    await orgs.set_quotas(org.id, quotas)
    return quotas_as_json(quotas)


# A set has no order and JSON has no set: the lending is answered sorted, so the same row always
# reads the same and a diff between two answers is a real one.
def quotas_as_json(quotas: Quotas) -> dict[str, Any]:
    """The quotas row as the operator reads it back."""
    dumped: dict[str, Any] = QUOTAS.dump_python(quotas, mode="json")
    dumped["lends"] = None if quotas.lends is None else sorted(quotas.lends)
    return dumped


# ── what it may dial ────────────────────────────────────────────────────────────


# Operator-only, and deliberately not beside `PUT /v1/org/judging`, which a tenant turns for
# itself: an org that could lift its own dialling fence has none. Replaced whole, as the quotas
# are, so a guard left out of the body goes back to the code's default rather than staying put.
@operator.put("/orgs/{named}/dialling")
async def set_dialling(
    named: str, said: WantedDialling, orgs: OrgsDep, policies: DialPoliciesDep
) -> dict[str, Any]:
    """Replace the org's outbound guards, whole. They bite the next dial."""
    org = await an_org(named, orgs)
    standing = DialPolicy()
    try:
        policy = DialPolicy(
            dial_anywhere=standing.dial_anywhere
            if said.dial_anywhere is None
            else said.dial_anywhere,
            per_minute=standing.per_minute if said.per_minute is None else said.per_minute,
            per_day=standing.per_day if said.per_day is None else said.per_day,
            max_duration_s=standing.max_duration_s
            if said.max_duration_s is None
            else said.max_duration_s,
        )
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    await policies.put(org.id, policy)
    dumped: dict[str, Any] = DIALLING.dump_python(policy)
    return dumped


# ── its keys ────────────────────────────────────────────────────────────────────


# The ONE response in the whole runtime that carries a key in the clear. It is built here, read
# once by the terminal that asked for it, and stored by nobody: docs/decisions/keys.md.
@operator.post("/orgs/{named}/keys")
async def issue(
    named: str, said: WantedKey, orgs: OrgsDep, keys: KeysDep, settings: SettingsDep
) -> dict[str, Any]:
    """Mint a key for the org, in this instance's world, and answer with it, the once. The table
    keeps its sha256."""
    org = await an_org(named, orgs)
    env = in_this_world(said.env, settings)
    try:
        scopes = KEY_SCOPES if said.scopes is None else key_scopes(said.scopes)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    issued = await keys.issue(
        org=org.id,
        label=said.label,
        env=env,
        scopes=scopes,
        subject=said.subject,
        name=said.name,
    )
    return issued.as_json


@operator.get("/orgs/{named}/keys")
async def keys_listed(named: str, orgs: OrgsDep, keys: KeysDep) -> list[dict[str, Any]]:
    """Every key of the org, oldest first, revoked ones included and named as revoked."""
    org = await an_org(named, orgs)
    return list(LISTED.dump_python(await keys.listed(org.id), mode="json"))


# A POST and not a DELETE, because nothing is deleted: the row stays and grows a timestamp, so the
# log entries that name this key stay readable. The verb on the wire says which of the two it is.
# It names no org: a fingerprint already names one row in the table.
@operator.post("/keys/{fingerprint}/revoke")
async def revoke(fingerprint: str, keys: KeysDep) -> dict[str, Any]:
    """Stop honouring one key from the next request. Its row, and its history, stay."""
    if not await keys.revoke(fingerprint):
        raise HTTPException(404, NO_SUCH_KEY.format(fingerprint=fingerprint))
    return {"fingerprint": fingerprint, "revoked": True}


def _as_json(org: Org) -> dict[str, Any]:
    """One org as the wire says it, through the adapter the listing already uses."""
    return dict(ORGS.dump_python((org,), mode="json")[0])
