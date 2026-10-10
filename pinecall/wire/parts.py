"""The parts events, commands and state share: the wire's words and its common shapes."""

from typing import Literal

from pydantic import AliasChoices, Field

from pinecall.domain.agent import EventSource
from pinecall.domain.names import Channel, Json, JsonObject
from pinecall.wire.frames import WireModel

# What a console may ask of the process standing in the agent's directory, relayed by the gateway.
type DevVerb = Literal[
    "chat.roster",
    "chat.start",
    "chat.say",
    "chat.end",
    "view.render",
    "simulate.start",
    "goldens.roster",
    "goldens.run",
    "knowledge.roster",
    "knowledge.push",
    "knowledge.eval",
    "memory.roster",
    "memory.eval",
    "memory.extraction",
    "promote.roster",
    "promote.write",
    "drift.read",
    "reproductions.roster",
    "reproductions.read",
]

# drained: the worker went down with the call on it; app_detached: the app closed mid-call.
type EndReason = Literal[
    "caller_hung_up",
    "agent_hung_up",
    "supervisor_ended",
    "transferred",
    "no_answer",
    "busy",
    "dial_failed",
    "timeout",
    "drained",
    "app_detached",
    "error",
]

# platform covers timeouts, errors and a drained worker.
type EndedBy = Literal["caller", "agent", "supervisor", "platform"]

# skipped: no model was reachable inside the call's judging budget.
type ScoreVerdict = Literal["held", "broken", "deferred", "skipped"]

# cold: a REFER on the caller's SIP leg. warm: the number is dialled into the room.
type TransferMode = Literal["cold", "warm"]

type PromptRegion = Literal["static", "dynamic"]
type DocsMode = Literal["retrieved", "tool"]
type PlatformTool = Literal["recall", "search"]
type UserState = Literal["listening", "speaking", "away"]
type AgentState = Literal["initializing", "idle", "listening", "thinking", "speaking"]
type ParticipantKind = Literal["caller", "agent", "supervisor", "listener", "sip"]
type TrackKind = Literal["audio", "video", "screen"]
type TrackSource = Literal["microphone", "camera", "screen_share", "screen_share_audio", "unknown"]
type Visibility = Literal["public", "tenant", "pii"]

# Which projection a sink applies before a state or an entry leaves the platform.
type Projection = Literal["public", "tenant"]

type CallStatus = Literal["idle", "ringing", "dialing", "active", "ended"]
type SessionFlag = Literal["escalated", "low_score", "promise"]
type ThreadKind = Literal["in", "out", "call"]
type WidgetTheme = Literal["auto", "light", "dark"]


# The provider of the box's own rows (its compute): what the platform cost, beside the vendors'.
PLATFORM = "pinecall"


class PromptBlockSpec(WireModel):
    """One named block of the prompt and the region it lives in."""

    name: str = Field(pattern="^[a-z][a-z0-9_]*$")
    region: PromptRegion


class Contact(WireModel):
    """Who is on the line, as far as the platform knows; a web visitor may be nobody yet."""

    id: str | None = None
    phone: str | None = None
    name: str | None = None
    email: str | None = None
    external_id: str | None = None


class Route(WireModel):
    """One door to an agent: a channel and, for phone and WhatsApp, the number that answers."""

    channel: Channel
    number: str | None
    label: str | None = None


class Supervisor(WireModel):
    """The human who sent a supervise verb, as the token that let them in names them."""

    id: str
    name: str | None = None


class ToolSpec(WireModel):
    """What the app declares about one tool."""

    name: str
    description: str
    parameters: JsonObject
    side_effect: Literal["read", "write", "irreversible"] = "read"
    confirm: str | None = None
    announce: str | None = None
    pii: list[str] | None = None
    timeout_s: float | None = None


class ToolResult(WireModel):
    """What came back from running a tool in the app's process: an output or an error."""

    call_id: str
    name: str
    output: Json = None
    error: str | None = None
    summary: str | None = None
    duration_s: float | None = None


class MemoryFact(WireModel):
    """One thing remembered about a contact."""

    id: str | None = None
    text: str
    category: str | None = None
    score: float | None = None
    source: str | None = None


class MemoryOp(WireModel):
    """One operation against the contact's memory."""

    op: Literal["recall", "remember", "forget"]
    contact: str | None = None
    query: str | None = None
    facts: list[MemoryFact]
    took_ms: float


class DocSource(WireModel):
    """One chunk of the knowledge base that retrieval put in front of the model for this turn."""

    id: str
    base: str | None = None
    path: str
    heading: str | None = None
    score: float
    excerpt: str | None = None


class CostRow(WireModel):
    """One priced line: a model, what was counted, how much, and what it came to."""

    provider: str
    model: str
    unit: Literal[
        "input_tokens",
        "cached_input_tokens",
        "cache_creation_tokens",
        "output_tokens",
        "characters",
        "audio_seconds",
        "requests",
        "session_seconds",
        "minutes",
    ]
    quantity: float
    unit_price_usd: float
    # A summary written before money in dollars priced its rows in euros: the same number, read as
    # dollars and never converted, as migration 0007 kept the facts' column.
    usd: float = Field(validation_alias=AliasChoices("usd", "eur"))


class UnpricedRow(WireModel):
    """A usage row the price table does not know: listed, never priced at zero."""

    provider: str
    model: str


class Cost(WireModel):
    """What the call cost in provider fees and, priced, the box's own compute; US dollars."""

    usd: float = Field(validation_alias=AliasChoices("usd", "eur"))
    rows: list[CostRow]
    unpriced: list[UnpricedRow]
    # The euro's rate a summary written before money in dollars carried; read, never written.
    rate: JsonObject | None = Field(default=None, exclude=True)


class VoiceConfig(WireModel):
    """Which voice speaks for the agent."""

    name: str | None = None
    provider: str | None = None
    model: str | None = None
    voice_id: str | None = None
    # A class of the vendor's livekit plugin other than its TTS, and its keyword arguments as
    # the plugin names them: what a class declares, over the operator's options for the vendor.
    builds: str | None = None
    options: JsonObject | None = None


class ModelConfig(WireModel):
    """Which model does a job (the LLM, or the STT), and the one or two knobs worth turning."""

    provider: str
    model: str
    temperature: float | None = None
    # A class of the vendor's livekit plugin other than its LLM or STT (`responses.LLM`), and its
    # keyword arguments as the plugin names them: what a class declares, over the operator's.
    builds: str | None = None
    options: JsonObject | None = None


class TurnConfig(WireModel):
    """How the session decides that the caller has finished, and when the caller may interrupt."""

    min_interruption_words: int | None = None
    endpointing_ms: int | None = None
    eot_threshold: float | None = None
    eager_eot_threshold: float | None = None
    min_interruption_ms: int | None = None


class Pronunciation(WireModel):
    """How the voice says one word it would otherwise get wrong."""

    word: str
    spoken: str


class StateFieldSpec(WireModel):
    """What the app declares about one field of its state: who may see it."""

    name: str
    visibility: Visibility


class ViewSpec(WireModel):
    """The panel the agent draws beside a conversation; its contents come by dev verb."""

    name: str


class EventSpec(WireModel):
    """One outside event the agent accepts, and from whom."""

    name: str
    from_: list[EventSource] = Field(alias="from")


class KnowledgeFile(WireModel):
    """One file of a base, as a push sends it: its path as the tenant keeps it, and its text."""

    path: str
    text: str


class DocsConfig(WireModel):
    """The knowledge base the agent answers from, and how its chunks reach the model."""

    base: str
    mode: DocsMode = "retrieved"
    k: int = 8
    min_score: float | None = None


class GreetingConfig(WireModel):
    """How the agent opens a call: `say` the words, or `reply` what the model reads first."""

    say: str | None = None
    reply: str | None = None
    allow_interruptions: bool | None = None


class HangupConfig(WireModel):
    """Whether the model may end the call itself, and when."""

    when: str = ""


class MemoryConfig(WireModel):
    """What memory keeps about a contact across calls, and what it must never keep."""

    remember: list[str] = Field(default_factory=list[str])
    forget: list[str] = Field(default_factory=list[str])


# Every field is optional so a configure can change one thing.
class AgentConfig(WireModel):
    """What an app declares about its agent."""

    prompt: list[PromptBlockSpec] | None = None
    language: str | None = None
    greeting: GreetingConfig | None = None
    voice: VoiceConfig | None = None
    llm: ModelConfig | None = None
    stt: ModelConfig | None = None
    turn: TurnConfig | None = None
    says: list[Pronunciation] | None = None
    hears: list[str] | None = None
    knowledge: KnowledgeFile | None = None
    docs: DocsConfig | None = None
    memory: MemoryConfig | None = None
    hangup: HangupConfig | None = None
    record: bool = True
    tools: list[ToolSpec] | None = None
    uses_knowledge: bool = False
    state_fields: list[StateFieldSpec] | None = None
    view: ViewSpec | None = None
    events: list[EventSpec] | None = None
