"""What an agent declares (prompt, tools, voice, turns) and what the org sets on top of it."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import Json, parse_slug

# public: the caller's browser. tenant: the console (default). pii: the console, masked.
type Visibility = Literal["public", "tenant", "pii"]


# app: the tenant's backend. participant: the caller's browser.
type EventSource = Literal["app", "participant"]


# irreversible tools (payments, cancellations) require the caller's confirmation, asked by the
# platform rather than the model.
type SideEffect = Literal["read", "write", "irreversible"]


# static: before the history, provider-cached. dynamic: after it, rewritten every turn.
type PromptRegion = Literal["static", "dynamic"]


# retrieved: top chunks are injected every turn. tool: the model gets a search(query) tool.
type DocsMode = Literal["retrieved", "tool"]


# every-call: at every hang-up the org judges. simulations: only a call a persona played.
type RunsOn = Literal["every-call", "simulations"]


# The names the hang-up panel gives its verdicts; a judge of the org's or an agent's takes another.
PANEL_JUDGES: tuple[str, ...] = ("consent", "grounded", "promises", "persona")


# A name every model vendor accepts as a function name.
_A_NAME_A_MODEL_CAN_CALL = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


# Written by the platform, never the app, so a knowledge file carries the operator's authority.
KNOWLEDGE = "knowledge"


DEFAULT_CHUNKS_PER_TURN = 8


# The default ends abandoned lines and machine-to-machine loops; 0 is no limit, else 1 to 60 min.
LONGEST_VOICE_CALL_S = 600


SHORTEST_LIMIT_S = 60


LONGEST_LIMIT_S = 3600


NO_LIMIT = 0


# Unset knobs are None, never "": an empty string reaches the vendor as a value (an empty voice
# once made every call silent).
BLANK = (
    "a blank {field} is not a change: an empty value once silenced a whole line of calls. "
    "Leave the field out to go back to what the app declared."
)


@dataclass(frozen=True)
class Voice:
    """The agent's TTS voice."""

    provider: str
    model: str | None = None
    voice_id: str | None = None


@dataclass(frozen=True)
class Model:
    """An LLM or STT model and its temperature."""

    provider: str
    model: str
    temperature: float | None = None


@dataclass(frozen=True)
class Turn:
    """Turn detection and interruption settings."""

    min_interruption_words: int | None = None
    endpointing_ms: int | None = None
    # Deepgram reports its default (0.7) ends up to a fifth of turns early.
    eot_threshold: float | None = None
    # The lower bar that starts LiveKit's speculative generation, offsetting a higher eot.
    eager_eot_threshold: float | None = None

    # Deepgram rejects the connection when eager > eot, leaving the call without STT.
    def __post_init__(self) -> None:
        if self.eager_eot_threshold is None or self.eot_threshold is None:
            return
        if self.eager_eot_threshold > self.eot_threshold:
            raise DeclarationRefused(
                f"eager_eot_threshold {self.eager_eot_threshold} is the bar for GUESSING the turn "
                f"is over, so it cannot sit above eot_threshold {self.eot_threshold}, which is the "
                "bar for ending it."
            )


@dataclass(frozen=True)
class Greeting:
    """The opening turn: fixed words (`say`) or an instruction for the model (`reply`)."""

    say: str | None = None
    reply: str | None = None
    allow_interruptions: bool | None = None

    def __post_init__(self) -> None:
        if (self.say is None) == (self.reply is None):
            text = "both were declared" if self.say is not None else "neither was"
            raise DeclarationRefused(
                "a greeting is one of two things: `say` the words, or `reply` what the model "
                f"reads before it finds its own. {text} — pick one."
            )


@dataclass(frozen=True)
class Hangup:
    """Lets the model end the call, with instructions on when."""

    when: str = ""


@dataclass(frozen=True)
class ToolSpec:
    """A tool as the app declares it, validated on construction."""

    name: str
    description: str
    parameters: Mapping[str, Json]
    side_effect: SideEffect = "read"
    pii: frozenset[str] = frozenset()
    confirm: str | None = None
    preview: int | None = None
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if not _A_NAME_A_MODEL_CAN_CALL.match(self.name):
            raise DeclarationRefused(f"a tool name is one word a model can call, not {self.name!r}")
        if not self.description.strip():
            raise DeclarationRefused(
                f"tool {self.name}: without a description no model can choose it"
            )
        if self.parameters.get("type") != "object":
            raise DeclarationRefused(
                f"tool {self.name}: parameters are a JSON Schema of type object"
            )
        if self.side_effect == "irreversible" and not self.confirm:
            raise DeclarationRefused(
                f"tool {self.name}: an irreversible tool needs a confirm template to read back"
            )
        if unknown := self.pii - self.parameter_names:
            raise DeclarationRefused(
                f"tool {self.name}: pii names parameters the tool has; unknown: {sorted(unknown)}"
            )
        if self.preview is not None and self.preview < 1:
            raise DeclarationRefused(f"tool {self.name}: a preview shows at least one item")
        if self.timeout_s <= 0:
            raise DeclarationRefused(f"tool {self.name}: timeout_s is a positive number of seconds")

    @property
    def parameter_names(self) -> frozenset[str]:
        """Return the property names of the parameter schema."""
        properties = self.parameters.get("properties")
        return frozenset(properties) if isinstance(properties, dict) else frozenset()


@dataclass(frozen=True)
class PromptBlock:
    """A named prompt block and its region."""

    name: str
    region: PromptRegion


@dataclass(frozen=True)
class Docs:
    """A knowledge base the agent uses, and how results reach the model."""

    base: str
    mode: DocsMode = "retrieved"
    k: int = DEFAULT_CHUNKS_PER_TURN
    min_score: float | None = None

    def __post_init__(self) -> None:
        if not self.base:
            raise DeclarationRefused("docs name the knowledge base they were pushed to")
        if self.k < 1:
            raise DeclarationRefused("docs hand the model at least one chunk")
        if self.min_score is not None and self.min_score < 0:
            raise DeclarationRefused("a fused rank score is never negative; min_score cannot be")


@dataclass(frozen=True)
class KnowledgeFile:
    """One file pushed to a knowledge base."""

    path: str
    text: str

    def __post_init__(self) -> None:
        if not self.path:
            raise DeclarationRefused("a knowledge file is named by its path")


# Free-text categories; anything under `forget` is never stored, whatever the model extracts.
@dataclass(frozen=True)
class MemoryPolicy:
    """What memory keeps about a contact across calls, and what it never keeps."""

    remember: tuple[str, ...] = ()
    forget: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if both := set(self.remember) & set(self.forget):
            raise DeclarationRefused(f"memory cannot both remember and forget {sorted(both)}")


@dataclass(frozen=True)
class Tuning:
    """One agent's settings; every knob is None until set."""

    voice: str | None = None
    tts: str | None = None
    tts_model: str | None = None
    stt: str | None = None
    llm: str | None = None
    greeting: Greeting | None = None
    hangup: Hangup | None = None
    turn: Turn | None = None
    memory: MemoryPolicy | None = None
    # Tri-state so an explicit False still overrides the layer below.
    record: bool | None = None
    # Voice calls only; 0 is no limit, None is the runtime default.
    max_duration_s: int | None = None
    # Markdown placed whole in the static knowledge block; editable with the `words` scope.
    knowledge: str | None = None
    # An empty tuple means "none" and overrides the layer below; None means unset.
    bases: tuple[Docs, ...] | None = None

    def __post_init__(self) -> None:
        knobs = (
            ("voice", self.voice),
            ("tts", self.tts),
            ("tts_model", self.tts_model),
            ("stt", self.stt),
            ("llm", self.llm),
            ("knowledge", self.knowledge),
        )
        for name, value in knobs:
            if value is not None and not value.strip():
                raise DeclarationRefused(BLANK.format(field=name))
        if self.max_duration_s is not None:
            _check_call_limit(self.max_duration_s)


@dataclass(frozen=True)
class Lexicon:
    """An agent's lexicon: TTS pronunciations and STT keyterms."""

    said: Mapping[str, str] = field(default_factory=dict[str, str])
    heard: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check_pronunciations(self.said, "the lexicon")
        if any(not word.strip() for word in self.heard):
            raise DeclarationRefused("the lexicon: a word the ears must know is not blank")


@dataclass(frozen=True)
class Version[T]:
    """A stored version of a value, with its holder, number, author and timestamp."""

    holder: str
    version: int
    author: str
    note: str | None
    set_at: datetime
    value: T


# Stored on the call's head row so the exact configuration can be read back later.
@dataclass(frozen=True)
class Versions:
    """Tuning and lexicon versions a call was built on; None where nothing was set."""

    config: int | None = None
    lexicon: int | None = None


@dataclass(frozen=True)
class AgentJudge:
    """A question the org wrote about one agent's job, put to the judge model at hang-up."""

    name: str
    question: str
    runs_on: RunsOn = "every-call"


DEFAULT_LAYOUT: tuple[PromptBlock, ...] = (
    PromptBlock("identity", "static"),
    PromptBlock(KNOWLEDGE, "static"),
    PromptBlock("tools", "static"),
    PromptBlock("view", "dynamic"),
)


# Field names match the wire's AgentConfig (tests/test_types.py holds them to it).
@dataclass(frozen=True)
class AgentConfig:
    """An agent's resolved declaration."""

    slug: str
    name: str | None = None
    prompt: tuple[PromptBlock, ...] = DEFAULT_LAYOUT
    greeting: Greeting | None = None
    language: str | None = None
    voice: Voice | None = None
    llm: Model | None = None
    stt: Model | None = None
    turn: Turn | None = None
    says: Mapping[str, str] = field(default_factory=dict[str, str])
    hears: tuple[str, ...] = ()
    # From Tuning.knowledge; placed whole in the static knowledge block.
    knowledge: str | None = None
    bases: tuple[Docs, ...] = ()
    # The class calls `this.knowledge.search`; registration fails when no base is attached.
    uses_knowledge: bool = False
    memory: MemoryPolicy | None = None
    hangup: Hangup | None = None
    record: bool = True
    max_duration_s: int = LONGEST_VOICE_CALL_S
    tools: tuple[ToolSpec, ...] = ()
    state_fields: Mapping[str, Visibility] = field(default_factory=dict[str, Visibility])
    # Side-panel title only; the console fetches its contents from the app, so tenant data
    # never passes through the gateway's config.
    view: str | None = None
    events: Mapping[str, frozenset[EventSource]] = field(
        default_factory=dict[str, frozenset[EventSource]]
    )

    def __post_init__(self) -> None:
        parse_slug(self.slug)
        names = [tool.name for tool in self.tools]
        if repeated := {name for name in names if names.count(name) > 1}:
            raise DeclarationRefused(f"agent {self.slug}: tool names repeat: {sorted(repeated)}")
        blocks = [block.name for block in self.prompt]
        if repeated := {name for name in blocks if blocks.count(name) > 1}:
            raise DeclarationRefused(
                f"agent {self.slug}: prompt block names repeat: {sorted(repeated)}"
            )
        _check_pronunciations(self.says, f"agent {self.slug}")
        _check_call_limit(self.max_duration_s)
        for event, sources in self.events.items():
            if not event or not sources:
                raise DeclarationRefused(
                    f"agent {self.slug}: event {event!r} names who may send it: app, participant"
                )

    def visibility_of(self, state_field: str) -> Visibility:
        """Return a state field's visibility, tenant unless declared."""
        return self.state_fields.get(state_field, "tenant")

    def accepts(self, event: str, source: EventSource) -> bool:
        """Return whether the event may be sent by this source."""
        return source in self.events.get(event, frozenset())

    @property
    def tools_by_name(self) -> Mapping[str, ToolSpec]:
        """Return the tools keyed by name."""
        return {tool.name: tool for tool in self.tools}


def _check_call_limit(seconds: int) -> None:
    """Raise DeclarationRefused unless seconds is 0 or between 60 and 3600."""
    if seconds != NO_LIMIT and not SHORTEST_LIMIT_S <= seconds <= LONGEST_LIMIT_S:
        raise DeclarationRefused(
            f"max_duration_s {seconds}: 0 for no limit, or {SHORTEST_LIMIT_S} to "
            f"{LONGEST_LIMIT_S} seconds (1 to 60 minutes)"
        )


def _check_pronunciations(says: Mapping[str, str], owner: str) -> None:
    """Raise DeclarationRefused when a word or its spoken form is blank."""
    if blank := {word for word, spoken in says.items() if not word or not spoken}:
        raise DeclarationRefused(
            f"{owner}: a pronunciation is a word and how it is said: {sorted(blank)}"
        )
