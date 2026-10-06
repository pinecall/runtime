"""The bodies of the box's own doors: orgs and what they hold, their people and keys, the box."""

from typing import Literal

from pydantic import Field

from pinecall.domain.names import Channel, Env
from pinecall.domain.org import QuotaName
from pinecall.wire.frames import Entry, WireModel
from pinecall.wire.rest.accounts import MemberRow
from pinecall.wire.rest.fleet import FleetTotals, WorkerStatus
from pinecall.wire.rest.numbers import DialGuards

# `bought` on the box's account; `twilio`, `sip`, `whatsapp` the org's account it was imported
# from; `imported` from an account since forgotten; `hooked` pointed at the box by the org itself;
# `typed` a row the box's operator wrote.
type NumberCameIn = Literal["bought", "twilio", "sip", "whatsapp", "imported", "hooked", "typed"]


class CreateOrgRequest(WireModel):
    """POST /v1/ops/orgs: the slug, and the name people read; the slug when none."""

    slug: str
    name: str | None = None


class OrgRow(WireModel):
    """One org: its minted id, its slug and its name."""

    id: str
    slug: str
    name: str


# The same shape read and written: a limit left out is none, and `lends` null lends everything.
class OrgQuotas(WireModel):
    """An org's limits in one world, by name; null is no limit, and `lends` comes back sorted."""

    limits: dict[QuotaName, int | None] = Field(default_factory=dict[QuotaName, int | None])
    budget_usd: int | None = None
    lends: list[str] | None = None


class PutQuotasRequest(WireModel):
    """PUT /v1/ops/orgs/{named}/quotas: which world, and the whole set for it."""

    env: Env
    quotas: OrgQuotas


class PutDiallingRequest(WireModel):
    """PUT /v1/ops/orgs/{named}/dialling: the guards whole; one left out is the default."""

    dial_anywhere: bool | None = None
    per_minute: int | None = None
    per_day: int | None = None
    max_duration_s: int | None = None


class OrgHolding(WireModel):
    """What the org holds right now, across both worlds, against its stock quotas."""

    memory_facts: int
    knowledge_chunks: int
    numbers: int
    seats: int


class OrgProfile(WireModel):
    """GET /v1/ops/orgs/{named}: one org, its quotas per world, its dial guards, its holdings."""

    id: str
    slug: str
    name: str
    quotas: dict[Env, OrgQuotas]
    dialling: DialGuards
    holding: OrgHolding


class MoveAgentRequest(WireModel):
    """PUT /v1/ops/orgs/{named}/agents: the agent to move into this org."""

    agent: str


class AgentMovedResponse(WireModel):
    """The move: how many logs came, which numbers came, which stayed where they were."""

    agent: str
    org: str
    logs: int
    numbers: list[str]
    stayed: list[str]


class IssueKeyRequest(WireModel):
    """POST /v1/ops/orgs/{named}/keys: a key of the org, in a world, with scopes and an owner."""

    env: Env
    label: str | None = None
    scopes: list[str] | None = None
    subject: str | None = None
    name: str | None = None


class OrgMembersResponse(WireModel):
    """GET /v1/ops/orgs/{named}/members: the org's people, and how many hold a seat."""

    members: list[MemberRow]
    seated: int


class OperatorRequest(WireModel):
    """PUT /v1/ops/orgs/{named}/members/{id}/operator: whether this member runs the box."""

    operator: bool


class SsoRequiredRequest(WireModel):
    """PUT /v1/ops/orgs/{named}/sso/required: whether a password may still open the org."""

    required: bool


class BoxMailResponse(WireModel):
    """GET and PUT /v1/ops/mail: the box's mailbox and where it came from, never the password."""

    configured: bool
    source: str | None
    host: str | None
    port: int | None
    security: str | None
    username: str | None
    from_: str | None = Field(alias="from")
    verified_at: str | None
    last_error: str | None


class BoxProvider(WireModel):
    """One box-wide identity provider: whether it is wired, the client, the URI to register."""

    configured: bool
    client_id: str | None
    redirect_uri: str


class BoxSignInResponse(WireModel):
    """GET /v1/ops/signin: every provider the box could offer every org's people."""

    google: BoxProvider


class PutSignInRequest(WireModel):
    """PUT /v1/ops/signin/google: the OAuth client the operator made for this gateway."""

    client_id: str
    client_secret: str


class PutBrandRequest(WireModel):
    """PUT /v1/ops/brand: a field left out keeps its value; an empty one goes to the default."""

    name: str | None = None
    logo_url: str | None = None
    accent: str | None = None


class RouteRequest(WireModel):
    """POST /v1/ops/routes: which org's agent answers a number, on a channel, in a world."""

    org: str
    number: str
    agent: str
    channel: Channel
    env: Env = "production"


class RouteRow(WireModel):
    """One route as the operator lists it."""

    org: str
    number: str
    agent: str
    channel: Channel
    env: Env
    managed: bool


class BoxNumber(WireModel):
    """GET /v1/ops/numbers: a number the box answers at, how it came, and who picks it up."""

    number: str
    channel: Channel
    org: str
    env: Env
    agent: str
    came_in: NumberCameIn
    # A process holds the agent in the org and world now: a call to the number is answered.
    running: bool
    # The catalog carrier a number the org hooked comes through, when it named one.
    via: str | None = None
    # False for a number the org hooked that waits for the operator: no call to it opens.
    approved: bool = True


class BoxCarrier(WireModel):
    """A carrier of the box's catalog: its published networks, and whether it is admitted."""

    kind: str
    name: str
    # The box drives its API (lists, points, buys numbers); the others are SIP terms alone.
    control: bool
    networks: list[str]
    source: str
    read_on: str
    admitted: bool
    # The box's own carrier: admitted always, its networks typed into nftables.conf.
    fixed: bool
    # The numbers of every org that reach the box through it.
    numbers: int


class FenceOpening(WireModel):
    """A network 5060 opens to beyond Twilio's, and why: a carrier's kind, or an org's ask."""

    network: str
    reason: str


class BoxFence(WireModel):
    """What the fence opens to beyond Twilio's, and every network the cloud's firewall admits."""

    openings: list[FenceOpening]
    # Twilio's first, then each opening: Terraform's `sip_sources` (`fence export`).
    networks: list[str]


class BoxCarriers(WireModel):
    """GET /v1/ops/carriers: the catalog, each carrier admitted or not, and the fence."""

    carriers: list[BoxCarrier]
    fence: BoxFence


class RepointRequest(WireModel):
    """POST /v1/ops/sip/repoint: the names each world was reached at before, sent on from."""

    former: dict[Env, list[str]] = Field(default_factory=dict[Env, list[str]])


class RepointedTrunk(WireModel):
    """POST /v1/ops/sip/repoint: a trunk of an account sent on from a world's name to its SIP's."""

    account: str
    trunk: str
    world: Env
    was: str
    now: str


class AdmitCarrierRequest(WireModel):
    """PUT /v1/ops/carriers/{kind}: whether orgs may bring numbers through the carrier."""

    admitted: bool


class CarrierNetworkRow(WireModel):
    """A network an org asked 5060 to open to: who, for what, and the operator's answer."""

    id: int
    org: str
    source: str
    network: str
    state: str
    asked_at: float
    decided_by: str | None
    decided_at: float | None


class FleetListed(WireModel):
    """GET /v1/ops/fleet: the box's time, every worker heard from, and each fleet summed."""

    now: float
    stale_after_s: float
    workers: list[WorkerStatus]
    totals: list[FleetTotals]


class FleetDemand(WireModel):
    """GET /v1/fleet/wanted: how many scaled workers the fleet wants, for KEDA."""

    fleet: str
    wanted: int
    # The fleet's calls and the seats its workers hold now, for whoever reads why.
    active: int
    seats: int


class BoxEvent(WireModel):
    """One frame of GET /v1/ops/events: an entry of some org's floor, whose, and in which world."""

    org: str
    env: Env | None = None
    entry: Entry


class TracebackCall(WireModel):
    """One phone call with the number: whose, which way, when, how it ended, whether erased."""

    call: str
    org: str | None
    env: str | None
    direction: str | None
    from_number: str | None
    to_number: str | None
    started_at: float | None
    ended_at: float | None
    end_reason: str | None
    # The call's log is gone and only its detail record is left (0016_call_records.sql).
    erased: bool


class TracebackDial(WireModel):
    """One dial to the number: whose, the call it placed, the number shown, who asked, the guard."""

    org: str
    env: str
    agent: str
    call: str | None
    shown: str | None
    asked_by: str
    refused: str | None
    at: float


class Traceback(WireModel):
    """GET /v1/ops/traceback: what the box keeps of one number since a day, oldest first."""

    number: str
    since: float
    calls: list[TracebackCall]
    dials: list[TracebackDial]
