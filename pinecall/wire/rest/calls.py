"""The call doors' bodies: readers, tokens, codes, seats, the worker's writes, threads, erasures."""

from typing import Literal

from pydantic import Field

from pinecall.domain.call import CallContext
from pinecall.domain.names import Channel, Direction, Env, Json, JsonObject
from pinecall.wire.frames import Entry, WireModel
from pinecall.wire.metrics import ModelUsage
from pinecall.wire.parts import (
    CallStatus,
    Contact,
    Cost,
    EndReason,
    PlatformTool,
    Projection,
    SessionFlag,
    ThreadKind,
)
from pinecall.wire.state import AttentionState

type ErasureSubject = Literal["call", "contact", "org"]

type ReadKind = Literal["log", "recording", "traceback", "listen", "supervise", "export", "memory"]


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


class CallRow(WireModel):
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


class CallList(WireModel):
    """A page of calls, newest first."""

    calls: list[CallRow]
    total: int | None = None
    next: str | None = None


class MintTokenRequest(WireModel):
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


class MintTokenResponse(WireModel):
    """livekit's token response, the call it opens and a token that reads its log."""

    server_url: str
    participant_token: str
    call: str
    log_token: str


class IssueCodeRequest(WireModel):
    """Four digits a caller keys to tie their call to a page."""

    agent: str
    ttl_s: int = 600
    log: Projection = "public"


class IssueCodeResponse(WireModel):
    """A code issued: the digits, the number to call, and the token its page polls with."""

    code: str
    number: str
    expires_at: float
    code_token: str


class CodeStatus(WireModel):
    """How a code stands; claimed, it names the call and a token that reads it."""

    code: str
    status: Literal["waiting", "claimed", "expired"]
    expires_at: float
    call: str | None
    log_token: str | None


class SeatResponse(WireModel):
    """A seat in a live call: the server, the token, and who it says the person is."""

    server_url: str
    participant_token: str
    call: str
    identity: str
    org: str
    subject: str | None
    name: str | None


class VerbResponse(WireModel):
    """A supervise verb queued; the call's log says what it did."""

    call: str
    verb: str
    seq: int | None


class OpenCallRequest(WireModel):
    """What a worker opens a call with, and says again to a gateway that forgot it."""

    agent: str
    context: CallContext
    # The app socket that must serve it (a spoken golden run); else the one the call reaches.
    app: str | None = None


class OpenCallResponse(WireModel):
    """What the org's minutes leave the call (null for no limit), and what it says first."""

    seconds_left: int | None
    minutes: int | None
    # An outbound call's AI disclosure, and the notice a recorded call says; null for none.
    disclosure: str | None = None
    recording_notice: str | None = None


class AppendEntryRequest(WireModel):
    """One entry a worker writes to its call's log."""

    type: str
    data: JsonObject
    ephemeral: bool | None = None


# `ts` is the worker's clock when the event happened; the gateway never stamps it past its own.
class BatchedEntry(WireModel):
    """One entry of a worker's batch: what it is, whether a store keeps it, and when it happened."""

    type: str
    data: JsonObject
    ephemeral: bool | None = None
    ts: float


# `after` makes a retry safe: the log answers a batch it already took with the seqs it gave it.
class AppendEntriesRequest(WireModel):
    """A worker's entries of its call, in order, after how many the log already took from it."""

    after: int = Field(ge=0)
    entries: list[BatchedEntry]


class AppendEntriesResponse(WireModel):
    """A worker's batch as the log numbered it, in the order it was sent."""

    entries: list[Entry]


# `input` is whole and unread by the wire: what recall and search take is the runtime's shape.
class LookupRequest(WireModel):
    """Which platform tool to run for a call's turn, what to run it with, and the turn it joins."""

    tool: PlatformTool
    input: JsonObject
    speech_id: str | None = None


class LookupResponse(WireModel):
    """What the tool found, as the model reads it, and how long finding it took."""

    output: JsonObject
    took_ms: float


class RememberResponse(WireModel):
    """How many memory operations a call produced, and how long remembering took."""

    ops: int
    took_ms: float


class RecordingKeyResponse(WireModel):
    """The key a call's recording is sealed under before it is stored: 32 bytes, base64url."""

    key: str


class SealCallRequest(WireModel):
    """The end of a call as its worker hands it to the gateway, which prices, judges and seals."""

    usage: list[ModelUsage]
    outcome: str
    recording: str | None = None
    # The vendors that ran on the box's own key: the operator bills their usage.
    lent: list[str] = Field(default_factory=list[str])


class CallbackRequest(WireModel):
    """Somebody the overflow told to wait for a call back."""

    agent: str
    channel: Channel
    number: str
    call: str | None = None


class CallbackRow(WireModel):
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


class CallbackList(WireModel):
    """A page of the org's callbacks, oldest first, and where the next starts."""

    requests: list[CallbackRow]
    next: int | None


class ThreadLast(WireModel):
    """The newest thing on a contact's thread."""

    text: str | None
    at: float
    kind: ThreadKind


class ThreadRow(WireModel):
    """One contact of an agent's inbox: every call of theirs, folded into one line."""

    contact: str
    name: str | None
    channel_last: Channel
    last: ThreadLast
    unread: int
    calls: int


class ThreadList(WireModel):
    """GET /v1/agents/{slug}/threads: the agent's contacts, the newest thread first."""

    threads: list[ThreadRow]
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


class ThreadResponse(WireModel):
    """GET /v1/agents/{slug}/threads/{contact}: every call of one contact, merged, oldest first."""

    contact: str
    name: str | None
    messages: list[ThreadMessage]


class ThreadMessageRequest(WireModel):
    """POST /v1/agents/{slug}/threads/{contact}/messages, the body."""

    text: str


class ThreadMessageResponse(WireModel):
    """POST /v1/agents/{slug}/threads/{contact}/messages, the answer: the call it was said on."""

    contact: str
    call: str


class Erasure(WireModel):
    """DELETE /v1/calls/{call}, /v1/contacts/{contact}: what one erasure took, as its trail says."""

    id: int
    at: float
    what: ErasureSubject
    subject: str
    env: Env | None
    asked_by: str
    calls: int
    entries: int
    memories: int
    recordings: int


class ReadRow(WireModel):
    """One read of the org's data: the call or number, what of it, who, and when."""

    subject: str
    what: ReadKind
    env: Env | None
    # A person's id, or "operator" for the box's operator.
    reader: str
    at: float


class ReadsResponse(WireModel):
    """GET /v1/org/reads: who read the org's calls, newest first."""

    reads: list[ReadRow]


class ErasureTrail(WireModel):
    """GET /v1/org/erasures: the org's erasures, newest first."""

    erasures: list[Erasure]
