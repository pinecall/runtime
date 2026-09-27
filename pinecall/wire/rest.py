"""The REST shapes: what a door takes and answers, and what a worker and the gateway exchange."""

from typing import Literal

from pydantic import Field

from pinecall.domain.types import CallContext, Channel, Direction, Env, Json, JsonObject, Route
from pinecall.wire.frames import WireModel
from pinecall.wire.metrics import ModelUsage
from pinecall.wire.parts import (
    CallStatus,
    Contact,
    Cost,
    EndReason,
    Projection,
    SessionFlag,
    WidgetTheme,
)
from pinecall.wire.state import AttentionState, State

# ── a call, as its readers see it ──


class CallState(WireModel):
    """A call's folded state, projected for the reader, and the cursor a stream resumes from."""

    state: State
    last_seq: int
    live: bool


# Projected entries keep only part of the envelope, so they travel as plain JSON.
class LogPage(WireModel):
    """One page of a log: the entries above the cursor, and where the next page starts."""

    entries: list[JsonObject]
    live: bool
    next: int | None


class SessionScore(WireModel):
    """What the judges said of a call, as a list row carries it."""

    held: int
    judged: int
    passed: bool
    reason: str | None


class SessionLine(WireModel):
    """One call as a list shows it."""

    call: str
    agent: str
    live: bool
    last_seq: int
    status: CallStatus
    channel: Channel | None
    direction: Direction | None
    from_: str | None = Field(alias="from")
    to: str | None
    caller: Contact | None
    started_at: float | None
    ended_at: float | None
    end_reason: EndReason | None
    outcome: str | None
    cost: Cost | None
    score: SessionScore | None = None
    flags: list[SessionFlag] | None = None
    attention: AttentionState | None = None


class SessionList(WireModel):
    """A page of calls, newest first."""

    calls: list[SessionLine]
    total: int | None = None
    next: str | None = None


# ── the agents an org holds, and whose terminal a ring lands in ──


class LineHolder(WireModel):
    """A corner by its member id and email; both null for the org's own."""

    holder: str | None
    name: str | None


class HeldAgent(WireModel):
    """An agent some socket of the org holds, the doors it answers, and whose corner."""

    slug: str
    channels: list[Channel]
    holder: LineHolder | None = None


class AgentList(WireModel):
    """The org's held agents in the request's world."""

    agents: list[HeldAgent]


class AppProcess(WireModel):
    """One connected app socket and what it holds."""

    app: str
    agents: list[str]
    env: Env
    host: str | None
    address: str | None
    sdk: str | None
    holder: LineHolder | None
    connected_at: float


class AppList(WireModel):
    """The app sockets of the org this key may see."""

    apps: list[AppProcess]


class AppStopped(WireModel):
    """A stop answered."""

    app: str
    stopped: bool


class TheLine(WireModel):
    """Whose terminal a ring at the agent lands in, and who else could take it."""

    agent: str
    env: Env
    held: bool
    holding: LineHolder | None = None
    yours: bool
    waiting: list[LineHolder]
    calling: list[str]


class Calling(WireModel):
    """The phone a developer calls from."""

    number: str


class NumberToCall(WireModel):
    """A production number and the agent it reaches."""

    number: str
    agent: str


class NumbersToCall(WireModel):
    """The person's own phones, and the production numbers they can dial to test."""

    calling: list[str]
    numbers: list[NumberToCall]


class Handed(WireModel):
    """Where a production ring goes: a developer's corner and its fleet, or nowhere (null)."""

    holder: str | None = None
    fleet: str | None = None


class Judging(WireModel):
    """Whether an org's calls are judged at hang-up, and the ceiling per call."""

    on: bool
    ceiling_eur: float | None


class JudgingWanted(WireModel):
    """Judging on or off."""

    on: bool


class WidgetSettings(WireModel):
    """How an agent's widget looks and opens."""

    title: str | None
    tagline: str | None
    greeting: str | None
    accent: str | None
    autostart: bool
    theme: WidgetTheme | None = None


class HoldAudio(WireModel):
    """What a caller hears while a tool runs: the box's melody, silence, or the org's own clip."""

    played: Literal["default", "off", "custom"]
    sha256: str | None = None
    seconds: float | None = None
    name: str | None = None


# ── a visitor, a page, a seat ──


class TokenWanted(WireModel):
    """livekit's token request body, and ours beside it."""

    agent: str | None = None
    scope: str = "talk"
    # An opaque contact id; never a number or a name.
    contact: str | None = None
    # Reaches the worker inside the signed dispatch: the browser reads it, never changes it.
    metadata: JsonObject = Field(default_factory=dict[str, Json])
    ttl_s: int | None = None
    log: Projection = "public"
    participant_identity: str | None = None
    participant_attributes: dict[str, str] = Field(default_factory=dict[str, str])
    room_config: JsonObject | None = None
    # Declared so they are refused with the reason instead of an unknown key.
    room_name: str | None = None
    participant_name: str | None = None
    participant_metadata: str | None = None


class TokenMinted(WireModel):
    """livekit's token response, the call it opens and a token that reads its log."""

    server_url: str
    participant_token: str
    call: str
    log_token: str


class CodeWanted(WireModel):
    """Four digits a caller keys to tie their call to a page."""

    agent: str
    ttl_s: int = 600
    log: Projection = "public"


class Code(WireModel):
    """A code issued: the digits, the number to call, and the token its page polls with."""

    code: str
    number: str
    expires_at: float
    code_token: str


class CodeStanding(WireModel):
    """How a code stands; claimed, it names the call and a token that reads it."""

    code: str
    status: Literal["waiting", "claimed", "expired"]
    expires_at: float
    call: str | None
    log_token: str | None


class SeatTaken(WireModel):
    """A seat in a live call: the server, the token, and who it says the person is."""

    server_url: str
    participant_token: str
    call: str
    identity: str
    org: str
    subject: str | None
    name: str | None


class VerbTaken(WireModel):
    """A supervise verb queued; the call's log says what it did."""

    call: str
    verb: str
    seq: int | None


# ── the worker and the gateway ──


class Opening(WireModel):
    """What a worker opens a call with, and says again to a gateway that forgot it."""

    agent: str
    context: CallContext
    # The app socket that must serve it (a spoken golden run); else the one the call reaches.
    app: str | None = None


class Opened(WireModel):
    """What the org's minutes leave the call, in seconds; null for no limit."""

    seconds_left: int | None
    minutes: int | None


class Appending(WireModel):
    """One entry a worker writes to its call's log."""

    type: str
    data: JsonObject
    ephemeral: bool | None = None


class Sealing(WireModel):
    """The end of a call as its worker hands it to the gateway, which prices, judges and seals."""

    usage: list[ModelUsage]
    outcome: str
    recording: str | None = None
    # The vendors that ran on the box's own key: the operator bills their usage.
    lent: list[str] = Field(default_factory=list[str])


class Heartbeat(WireModel):
    """A worker's report: its fleet, its name, what it holds and how loaded it is."""

    fleet: str
    worker: str
    active: int
    # Measured slots; null when the worker is gated on its machine's CPU.
    max_jobs: int | None
    load: float
    draining: bool


class Standing(WireModel):
    """The answer to a heartbeat: whether this worker is cordoned, and its fleet full."""

    cordoned: bool
    full: bool


class FleetTotals(WireModel):
    """One fleet over the workers heard from lately."""

    fleet: str
    workers: int
    active: int
    seats: int
    free: int
    accepting: int
    full: bool


class CallbackWanted(WireModel):
    """Somebody the overflow told to wait for a call back."""

    agent: str
    channel: Channel
    number: str
    call: str | None = None


class CallbackTaken(WireModel):
    """One call back somebody asked for, as the org's list shows it."""

    position: int
    agent: str
    ts: float
    channel: Channel
    number: str
    via: Literal["overflow", "widget", "agent"]
    call: str | None
    when: str | None = None
    note: str | None = None
    contact: Contact | None = None


class CallbacksPage(WireModel):
    """A page of the org's callbacks, oldest first, and where the next starts."""

    requests: list[CallbackTaken]
    next: int | None


# ── dialling out ──


class Dialled(WireModel):
    """POST /v1/agents/{slug}/dial, the answer."""

    call: str
    agent: str
    to: str
    from_: str = Field(alias="from")
    env: Env
    log_token: str


class DialGuards(WireModel):
    """GET /v1/carrier/outbound, the guards: what this org may dial and how often."""

    dial_anywhere: bool
    per_minute: int
    per_day: int
    max_duration_s: int


class CarrierOutbound(WireModel):
    """GET /v1/carrier/outbound, the answer."""

    ready: bool
    kind: str | None
    from_numbers: list[str]
    steps_missing: list[str]
    guards: DialGuards


class OutboundProvisioned(WireModel):
    """POST /v1/carrier/outbound, the answer: the plan, and whether it was carried out."""

    steps: list[str]
    dry_run: bool
    ready: bool
    trunk: str | None = None
    address: str | None = None


# ── an agent's inbox, by contact ──

type ThreadKind = Literal["in", "out", "call"]


class ThreadLast(WireModel):
    """The newest thing on a contact's thread."""

    text: str | None
    at: float
    kind: ThreadKind


class ThreadLine(WireModel):
    """One contact of an agent's inbox: every call of theirs, folded into one line."""

    contact: str
    name: str | None
    channel_last: Channel
    last: ThreadLast
    unread: int
    calls: int


class ThreadList(WireModel):
    """GET /v1/agents/{slug}/threads: the agent's contacts, the newest thread first."""

    threads: list[ThreadLine]
    next: str | None


class ThreadMessage(WireModel):
    """One message of a thread, or one spoken call drawn as a pill."""

    kind: ThreadKind
    text: str | None
    at: float
    call: str
    channel: Channel
    duration_s: float | None = None
    answered: bool | None = None


class Thread(WireModel):
    """GET /v1/agents/{slug}/threads/{contact}: every call of one contact, merged, oldest first."""

    contact: str
    name: str | None
    messages: list[ThreadMessage]


class ThreadSay(WireModel):
    """POST /v1/agents/{slug}/threads/{contact}/messages, the body."""

    text: str


class ThreadSaid(WireModel):
    """POST /v1/agents/{slug}/threads/{contact}/messages, the answer: the call it was said on."""

    contact: str
    call: str


# ── numbers and the accounts they live in ──


class CarrierBrought(WireModel):
    """GET /v1/carrier: one account of the org, named, never its secret."""

    kind: str
    account: str
    label: str = ""


class CarriersBrought(WireModel):
    """GET /v1/carriers: every account of the org, oldest first."""

    carriers: list[CarrierBrought]


class NumberOwned(WireModel):
    """A number an account owns, and whether this world imported it."""

    number: str
    name: str
    imported: bool
    account: str


class NumbersAvailable(WireModel):
    """GET /v1/numbers/available: what the org's Twilio accounts own; a peer lists none."""

    kind: str
    numbers: list[NumberOwned]


class NumberAnswering(WireModel):
    """GET /v1/numbers, one row: a number of the org and the agent it reaches."""

    route: Route


class NumberRouted(WireModel):
    """POST /v1/numbers and /v1/numbers/buy: the route, one sentence a step, whether it ran."""

    route: Route
    steps: list[str]
    dry_run: bool


class NumberWanted(WireModel):
    """POST /v1/numbers: a number of one of the org's accounts, or one the org hooks itself."""

    number: str
    agent: str
    channel: Channel = "phone"
    account: str | None = None
    hooked: bool = False
    networks: list[str] = Field(default_factory=list[str])
    move: bool = False


class PurchaseWanted(WireModel):
    """POST /v1/numbers/buy: a number wanted from the box's own account."""

    country: str = Field(min_length=2, max_length=2)
    area_code: str | None = None
    agent: str
    channel: Channel = "phone"


class NumberMoved(WireModel):
    """PUT /v1/numbers/{number}/env: the world the number answers in from now on."""

    env: Env


class CallWanted(WireModel):
    """POST /v1/agents/{slug}/dial: the far end, and the org's number it is shown."""

    to: str
    from_: str | None = Field(None, alias="from")
    # What the log token handed back reads the call through.
    log: Projection = "tenant"


class LegTrunk(WireModel):
    """How the worker dials a leg: the host, its transport, the pair, the number shown."""

    hostname: str
    transport: Literal["auto", "udp", "tcp", "tls"]
    username: str
    password: str
    shown: str


class LegDialled(WireModel):
    """GET /v1/agents/{slug}/outbound-trunk: the leg's trunk, inline, after the guards."""

    trunk: LegTrunk
