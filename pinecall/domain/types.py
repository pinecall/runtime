"""The domain: what an agent declares, what a call is, who owns it, and what an org may do."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pinecall.domain.errors import DeclarationRefused

type Json = str | int | float | bool | list[Json] | dict[str, Json] | None
type JsonObject = dict[str, Json]

# ── worlds, channels, names ──

# A key belongs to one environment; its agents, numbers and calls are isolated to it. A shared
# "staging" is a sandbox agent registered by a machine key (no holder), not a third environment.
type Env = Literal["production", "sandbox"]
ENVS: tuple[Env, ...] = ("production", "sandbox")
PRODUCTION: Env = "production"
SANDBOX: Env = "sandbox"

# phone: SIP into a LiveKit room; web: the widget over WebRTC; whatsapp: text.
type Channel = Literal["phone", "web", "whatsapp"]
CHANNELS: tuple[Channel, ...] = ("phone", "web", "whatsapp")
CHANNELS_WITH_A_NUMBER: tuple[Channel, ...] = ("phone", "whatsapp")
THE_WIDGET: Channel = "web"

type Direction = Literal["inbound", "outbound"]

# e.g. clinica-norte: an agent's slug, and an org's.
A_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_E164 = re.compile(r"^\+[1-9]\d{1,14}$")

# Checked on every address before a letter is sent: whitespace would allow header injection
# (RFC 5322 §2.2).
AN_ADDRESS = re.compile(r"^[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+$")


def parse_env(word: str) -> Env:
    """Return the word as an Env, raising DeclarationRefused when it is neither world."""
    if word not in ENVS:
        raise DeclarationRefused(f"a key opens one of {sorted(ENVS)}, not {word!r}")
    return word


def parse_channel(word: str) -> Channel:
    """Return the word as a Channel, raising DeclarationRefused when it is none."""
    if word not in CHANNELS:
        raise DeclarationRefused(f"a call comes through one of {sorted(CHANNELS)}, not {word!r}")
    return word


def parse_slug(word: str) -> str:
    """Return the slug, raising DeclarationRefused unless it is lowercase words joined by dashes."""
    if not A_SLUG.match(word):
        raise DeclarationRefused(f"a slug is lowercase words joined by dashes, not {word!r}")
    return word


def dialable(number: str) -> bool:
    """Return whether the number is in E.164 form."""
    return _E164.match(number) is not None


def parse_e164(number: str) -> str:
    """Return the trimmed number, raising DeclarationRefused when it is not E.164."""
    said = number.strip()
    if not dialable(said):
        raise DeclarationRefused(
            f"a number is written in E.164 form, like +59829001199, not {said!r}"
        )
    return said


def is_a_deployment(env: Env) -> bool:
    """Return whether the environment is org-wide rather than a person's sandbox."""
    return env != SANDBOX


# ── an agent's declaration ──

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
            said = "both were declared" if self.say is not None else "neither was"
            raise DeclarationRefused(
                "a greeting is one of two things: `say` the words, or `reply` what the model "
                f"reads before it finds its own. {said} — pick one."
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


DEFAULT_LAYOUT: tuple[PromptBlock, ...] = (
    PromptBlock("identity", "static"),
    PromptBlock(KNOWLEDGE, "static"),
    PromptBlock("tools", "static"),
    PromptBlock("view", "dynamic"),
)


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


def check_call_limit(seconds: int) -> None:
    """Raise DeclarationRefused unless seconds is 0 or between 60 and 3600."""
    if seconds != NO_LIMIT and not SHORTEST_LIMIT_S <= seconds <= LONGEST_LIMIT_S:
        raise DeclarationRefused(
            f"max_duration_s {seconds}: 0 for no limit, or {SHORTEST_LIMIT_S} to "
            f"{LONGEST_LIMIT_S} seconds (1 to 60 minutes)"
        )


def check_pronunciations(says: Mapping[str, str], whose: str) -> None:
    """Raise DeclarationRefused when a word or its spoken form is blank."""
    if blank := {word for word, spoken in says.items() if not word or not spoken}:
        raise DeclarationRefused(
            f"{whose}: a pronunciation is a word and how it is said: {sorted(blank)}"
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
        check_pronunciations(self.says, f"agent {self.slug}")
        check_call_limit(self.max_duration_s)
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


# ── a call ──

# Prefix of call ids minted here (`call_` + 32 hex); phone calls are named by the media plane.
A_CALL = "call_"


@dataclass(frozen=True)
class Contact:
    """What is known about the caller; a web visitor may be anonymous."""

    id: str | None = None
    phone: str | None = None
    name: str | None = None
    email: str | None = None
    external_id: str | None = None


@dataclass(frozen=True)
class Route:
    """Which agent answers on a channel and number, and which org owns it."""

    org: str
    agent: str
    channel: Channel
    number: str | None = None
    label: str | None = None
    # A number belongs to one environment; the registry refuses it in the other.
    env: Env = PRODUCTION
    # Bought on the box's carrier account, so it counts against the `numbers` quota; numbers
    # imported from the tenant's own account do not.
    managed: bool = False

    def __post_init__(self) -> None:
        if not self.org or not self.agent:
            raise DeclarationRefused(
                "a route names the org that owns it and the agent that answers"
            )
        if self.channel in CHANNELS_WITH_A_NUMBER:
            if self.number is None or not dialable(self.number):
                raise DeclarationRefused(
                    f"a {self.channel} route answers at a number in E.164 form, not {self.number!r}"
                )
        elif self.number is not None:
            raise DeclarationRefused(f"the {self.channel} widget answers at no number")

    @property
    def door(self) -> tuple[Channel, str | None]:
        """Return (channel, number): the registry's key, whatever the org or the label."""
        return (self.channel, self.number)


# Immutable: whatever changes during a call is recorded in the log.
@dataclass(frozen=True)
class CallContext:
    """A call as the worker and the gateway see it when it starts."""

    call: str
    channel: Channel
    direction: Direction
    caller: str
    route: Route
    today: date
    contact: Contact | None = None
    metadata: Mapping[str, Json] = field(default_factory=dict[str, Json])
    # Eval run that opened the call; None for a real caller.
    run: str | None = None
    # Simulated caller's name, and its rules frozen at call start so judging ignores later edits.
    persona: str | None = None
    accepts_when: str | None = None
    declines_when: str | None = None
    # Sandbox developer the call belongs to; None for the org itself.
    holder: str | None = None

    def __post_init__(self) -> None:
        if not self.call:
            raise DeclarationRefused("a call context names its call")
        if not self.caller:
            raise DeclarationRefused("a call has a calling side: a number, or the visitor id")
        if self.route.channel != self.channel:
            raise DeclarationRefused(
                f"a {self.channel} call cannot come through a {self.route.channel} route"
            )

    @property
    def env(self) -> Env:
        """Return the environment of the call's route."""
        return self.route.env

    # The app's contact id, else the number, so phone and WhatsApp calls share one contact.
    @property
    def remembered_as(self) -> str | None:
        """Return the memory key for the caller, or None when the caller is anonymous."""
        if self.contact is not None and self.contact.id:
            return self.contact.id
        return self.caller if self.channel in CHANNELS_WITH_A_NUMBER else None


def new_call_id() -> str:
    """Mint a call id; it is also the LiveKit room name."""
    return f"{A_CALL}{uuid4().hex}"


# ── an org and its limits ──

# Seeded by the schema; the first key is issued against it and CLI verbs fall back to it.
DEFAULT_ORG = "default"

# Names match the DB columns and the wire. The first four are flows (consumed or open now); the
# rest are stocks (rows kept), which lets a plan disable a feature with a zero.
type QuotaName = Literal[
    "minutes",
    "messages",
    "agents",
    "concurrent_calls",
    "memory_facts",
    "knowledge_chunks",
    "numbers",
    "seats",
    "llm_tokens",
]
QUOTAS: tuple[QuotaName, ...] = (
    "minutes",
    "messages",
    "agents",
    "concurrent_calls",
    "memory_facts",
    "knowledge_chunks",
    "numbers",
    "seats",
    "llm_tokens",
)


@dataclass(frozen=True)
class Org:
    """An organisation: immutable id, editable slug, and display name."""

    id: str
    slug: str
    name: str

    def __post_init__(self) -> None:
        if not self.id:
            raise DeclarationRefused("an org has an id")
        parse_slug(self.slug)


# None is no limit (the self-hosted default). Zero is a real limit that refuses everything.
@dataclass(frozen=True)
class Quotas:
    """An org's quota limits, plus its monthly budget and the box keys lent to it."""

    minutes: int | None = None
    messages: int | None = None
    agents: int | None = None
    concurrent_calls: int | None = None
    memory_facts: int | None = None
    knowledge_chunks: int | None = None
    # Only numbers bought on the box's carrier account; imported numbers do not count.
    numbers: int | None = None
    # Invited and active members; disabled members hold no seat.
    seats: int | None = None
    llm_tokens: int | None = None
    # Monthly budget in whole euros. Informational: nothing is refused over it.
    budget_eur: int | None = None
    # Box vendor keys the org may use: None all, empty none, else `vendor` or `vendor/model`.
    lends: frozenset[str] | None = None

    def __post_init__(self) -> None:
        for name, limit in self.limits.items():
            if limit is not None and limit < 0:
                raise DeclarationRefused(f"a quota is a count, and {name} cannot be {limit}")
        if self.budget_eur is not None and self.budget_eur < 0:
            raise DeclarationRefused(f"a budget is euros, and cannot be {self.budget_eur}")

    @property
    def limits(self) -> Mapping[QuotaName, int | None]:
        """Return every limit by its name."""
        return {
            "minutes": self.minutes,
            "messages": self.messages,
            "agents": self.agents,
            "concurrent_calls": self.concurrent_calls,
            "memory_facts": self.memory_facts,
            "knowledge_chunks": self.knowledge_chunks,
            "numbers": self.numbers,
            "seats": self.seats,
            "llm_tokens": self.llm_tokens,
        }

    def reached(self, quota: QuotaName, used: float) -> int | None:
        """Return the limit when `used` is at or past it, else None."""
        limit = self.limits[quota]
        return limit if limit is not None and used >= limit else None

    def exceeded(self, quota: QuotaName, keeping: float) -> int | None:
        """Return the limit when `keeping` would pass it, else None; for pushes sized up front."""
        limit = self.limits[quota]
        return limit if limit is not None and keeping > limit else None

    def switched_off(self, quota: QuotaName) -> bool:
        """Return whether the quota is zero: the feature is not in the plan."""
        return self.limits[quota] == 0


# ── people and keys ──

# Every gateway door belongs to exactly one scope; `fleet` is the box's worker, resolved per call
# instead of per key org, and only `keys issue --scope fleet` grants it.
type KeyScope = Literal[
    "app",
    "calls",
    "talk",
    "supervise",
    "pipeline",
    "knowledge",
    "memory",
    "evals",
    "numbers",
    "keys",
    "providers",
    "team",
    "usage",
    "words",
    "fleet",
]
EVERY_SCOPE: tuple[KeyScope, ...] = (
    "app",
    "calls",
    "talk",
    "supervise",
    "pipeline",
    "knowledge",
    "memory",
    "evals",
    "numbers",
    "keys",
    "providers",
    "team",
    "usage",
    "words",
    "fleet",
)
THE_FLEET: KeyScope = "fleet"
HOLDING: KeyScope = "app"
THE_TEAM: KeyScope = "team"

# Default scopes: every scope except fleet.
KEY_SCOPES: frozenset[KeyScope] = frozenset(EVERY_SCOPE) - {THE_FLEET}

# Holder value for org-owned rows: an empty string, not NULL, because it is part of a primary
# key and NULL never matches.
THE_ORGS_OWN = ""

# A role is only a preset of key scopes. Doors check scopes, never roles, so changing a role
# affects newly issued keys only.
type Role = Literal["qa", "supervisor", "manager", "admin", "developer"]
ROLES: tuple[Role, ...] = ("qa", "supervisor", "manager", "admin", "developer")
ROLE_SCOPES: Mapping[Role, frozenset[KeyScope]] = {
    "qa": frozenset({"calls", "evals"}),
    "supervisor": frozenset({"calls", "evals", "supervise", "talk", "memory", "words"}),
    "manager": frozenset(
        {
            "calls",
            "evals",
            "supervise",
            "talk",
            "memory",
            "numbers",
            "keys",
            "providers",
            "usage",
            "team",
            "words",
        }
    ),
    "admin": KEY_SCOPES,
    "developer": frozenset(
        {"app", "calls", "talk", "supervise", "pipeline", "knowledge", "memory", "evals", "words"}
    ),
}

# disabled: cannot log in and keys are revoked; the row is kept because the log references it.
type MemberStatus = Literal["invited", "active", "disabled"]


def key_scopes(words: Iterable[str]) -> frozenset[KeyScope]:
    """Return the words as scopes, raising DeclarationRefused on the first that is none."""
    scopes: set[KeyScope] = set()
    for word in sorted(set(words)):
        if word not in EVERY_SCOPE:
            raise DeclarationRefused(
                f"{word!r} is not a key scope; the scopes are {sorted(EVERY_SCOPE)}"
            )
        scopes.add(word)
    return frozenset(scopes)


def whose(holder: str | None) -> str:
    """Return the holder column value; None is the org's own."""
    return THE_ORGS_OWN if holder is None else holder


def parse_role(word: str) -> Role:
    """Return the word as a Role, raising DeclarationRefused when it is none."""
    if word not in ROLES:
        raise DeclarationRefused(f"a role is one of {sorted(ROLES)}, not {word!r}")
    return word


@dataclass(frozen=True)
class Member:
    """A person in one org, with their role, agents and status."""

    id: str
    org: str
    email: str
    name: str
    role: Role
    # Empty means all of the org's agents.
    agents: frozenset[str] = frozenset()
    status: MemberStatus = "invited"
    # Box operator: their key also opens /v1/ops/*. Granted only with the ops key.
    operator: bool = False
    # Granted by an admin; checked per request, so revoking it takes effect immediately.
    production: bool = False
    # Email ownership proven by a mailed link, an IdP, or an operator invitation. Required
    # before joining another org without a link.
    verified: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.org:
            raise DeclarationRefused("a member names their id and the org they belong to")
        if not AN_ADDRESS.match(self.email):
            raise DeclarationRefused(f"an email has one @ and a domain, not {self.email!r}")
        if not self.name.strip():
            raise DeclarationRefused("a member has a name: it is what a seat says when they sit")

    @property
    def scopes(self) -> frozenset[KeyScope]:
        """Return the scopes of keys minted for this member."""
        return ROLE_SCOPES[self.role]

    @property
    def opens_production(self) -> bool:
        """Return whether this member may act in production; admins always may."""
        return self.role == "admin" or self.production


# Doors take the org only from this record, never from the request.
@dataclass(frozen=True)
class Key:
    """A verified API key: its org, environment, scopes and holder."""

    key_id: str
    org: str
    label: str | None = None
    env: Env = PRODUCTION
    scopes: frozenset[KeyScope] = KEY_SCOPES
    # The member a personal key was minted for; None on org keys.
    subject: str | None = None
    name: str | None = None
    # Per-request only (the `pinecall-corner` header); never stored.
    looking_at: str | None = None
    # None never expires. Keys minted from a personal key never outlive it.
    expires_at: datetime | None = None


# ── what is set on top of the declaration ──

# Unset knobs are None, never "": an empty string reaches the vendor as a value (an empty voice
# once made every call silent).
BLANK = (
    "a blank {field} is not a change: an empty value once silenced a whole line of calls. "
    "Leave the field out to go back to what the app declared."
)


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
            check_call_limit(self.max_duration_s)


# Org-wide, applied on top of every agent's own pronunciations.
@dataclass(frozen=True)
class Lexicon:
    """The org's lexicon: TTS pronunciations and STT keyterms."""

    said: Mapping[str, str] = field(default_factory=dict[str, str])
    heard: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check_pronunciations(self.said, "the lexicon")
        if any(not word.strip() for word in self.heard):
            raise DeclarationRefused("the lexicon: a word the ears must know is not blank")


@dataclass(frozen=True)
class Kept[T]:
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


# ── the day ──


def parse_zone(zone: str) -> ZoneInfo:
    """Return the IANA zone, raising DeclarationRefused when there is none by that name."""
    try:
        return ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as unknown:
        raise DeclarationRefused(
            f"{zone!r} is not an IANA time zone (Europe/Madrid, America/Montevideo, UTC)"
        ) from unknown


def today_in(zone: str) -> date:
    """Return today's date in the zone: a call is dated where the caller is, not the box."""
    return datetime.now(UTC).astimezone(parse_zone(zone)).date()
