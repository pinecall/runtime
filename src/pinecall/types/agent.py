"""AgentConfig: everything an app declares about one agent, whole and frozen, for both processes."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, get_args

from pinecall.types.knowledge import Docs, MemoryPolicy
from pinecall.types.prompt import DEFAULT_LAYOUT, PromptBlock
from pinecall.types.refused import DeclarationRefused
from pinecall.types.tool import ToolSpec

# Who may see a field of the app's state: the caller's own browser (public), the tenant's console
# (tenant), or the console with the value masked (pii). Undeclared means tenant.
type Visibility = Literal["public", "tenant", "pii"]

# Who may hand the agent an outside fact: the tenant's backend, or the caller's browser.
type EventSource = Literal["app", "participant"]

VISIBILITIES: frozenset[str] = frozenset(get_args(Visibility.__value__))
EVENT_SOURCES: frozenset[str] = frozenset(get_args(EventSource.__value__))

# The slug is the agent's name on the wire and in a URL: clinica-norte, never Clínica Norte.
_A_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Voice:
    """Which voice speaks for the agent."""

    provider: str
    model: str | None = None
    voice_id: str | None = None


@dataclass(frozen=True)
class Model:
    """Which model does a job, the LLM or the STT, and the one knob worth turning."""

    provider: str
    model: str
    temperature: float | None = None


# Deepgram refuses a socket whose eager bar sits above its real one, and a refused socket is a
# call with no ears at all — so it is said here, once, where the class is declared.
EAGER_IS_THE_LOWER_BAR = (
    "eager_eot_threshold {eager} is the bar for GUESSING the turn is over, so it cannot sit above "
    "eot_threshold {sure}, which is the bar for ending it."
)


@dataclass(frozen=True)
class Turn:
    """How the session decides the caller is done, and how much it takes to interrupt the agent."""

    min_interruption_words: int | None = None
    endpointing_ms: int | None = None
    # How sure a recogniser that calls the end of the turn itself has to be. Deepgram's own
    # measurement of its default (0.7) is that as much as a fifth of the turns it ends were ended
    # before the caller had finished — half a sentence answered, and a tool run on half the facts.
    eot_threshold: float | None = None
    # And the lower bar at which it says the turn MIGHT be over, which is what livekit's
    # speculative generation hangs off: it is how the bar above is raised without paying latency.
    eager_eot_threshold: float | None = None

    def __post_init__(self) -> None:
        if self.eager_eot_threshold is None or self.eot_threshold is None:
            return
        if self.eager_eot_threshold > self.eot_threshold:
            raise DeclarationRefused(
                EAGER_IS_THE_LOWER_BAR.format(
                    eager=self.eager_eot_threshold, sure=self.eot_threshold
                )
            )


# The two verbs of agent.say and agent.reply, declared instead of called: the session runs one of
# them the moment it opens, and a class that declares nothing here waits for the caller.
GREETING_IS_ONE_VERB = (
    "a greeting is one of two things: `say` the words, or `reply` what the model reads before "
    "it finds its own. {said} — pick one."
)


@dataclass(frozen=True)
class Greeting:
    """How the agent opens a call: the words themselves, or the instruction the model answers."""

    say: str | None = None
    reply: str | None = None
    allow_interruptions: bool | None = None

    def __post_init__(self) -> None:
        if (self.say is None) == (self.reply is None):
            said = "both were declared" if self.say is not None else "neither was"
            raise DeclarationRefused(GREETING_IS_ONE_VERB.format(said=said))


# Declaring this is what puts livekit's own end_call in front of the model. A class that says
# nothing here cannot hang up, and a call ends when the caller does or when a supervisor says so.
@dataclass(frozen=True)
class Hangup:
    """Whether the model may end the call itself, and when, in the tenant's own words."""

    when: str = ""


# Field names agree with the wire's AgentConfig, so the gateway maps one onto the other by name;
# tests/types/test_wire_agreement.py holds them to it.
@dataclass(frozen=True)
class AgentConfig:
    """What an app declared about one agent, resolved: the runtime reads this and asks no more."""

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
    # What the agent knows by heart, in Markdown: the org's own words, read whole into the static
    # knowledge block of every call. The world's (Tuning.knowledge), never the class's.
    knowledge: str | None = None
    # Every base the world attached, each with how a turn reads it: the RAG. The world's too
    # (Tuning.bases); the resolver fills it (api/agents/tuned.py). A settings row that attaches
    # none and one that attaches an empty list run the same session — the difference between them
    # is which corner is heard, and that is spent by the time a config is built.
    bases: tuple[Docs, ...] = ()
    # Whether the class searches the base itself, `this.knowledge.search`: a world that attaches
    # none refuses the registration, so a tool that would find nothing is refused at boot.
    uses_knowledge: bool = False
    memory: MemoryPolicy | None = None
    hangup: Hangup | None = None
    # Whether this agent's calls keep their audio. The world's (Tuning.record), never the class's;
    # the box records the whole room, so what is kept is what everybody on the call heard.
    record: bool = True
    tools: tuple[ToolSpec, ...] = ()
    state_fields: Mapping[str, Visibility] = field(default_factory=dict[str, Visibility])
    # The panel the agent draws beside a conversation, by the name a person reads over it. Only
    # the NAME is declared: a console asks the app itself for what the panel holds, one
    # conversation at a time (api/agents/dev.py, the `view` family), because what it holds is the
    # tenant's own data and never the gateway's.
    view: str | None = None
    events: Mapping[str, frozenset[EventSource]] = field(
        default_factory=dict[str, frozenset[EventSource]]
    )

    def __post_init__(self) -> None:
        if not _A_SLUG.match(self.slug):
            raise DeclarationRefused(
                f"an agent's slug is lowercase words joined by dashes, not {self.slug!r}"
            )
        names = [tool.name for tool in self.tools]
        if repeated := {name for name in names if names.count(name) > 1}:
            raise DeclarationRefused(f"agent {self.slug}: tool names repeat: {sorted(repeated)}")
        blocks = [block.name for block in self.prompt]
        if repeated := {name for name in blocks if blocks.count(name) > 1}:
            raise DeclarationRefused(
                f"agent {self.slug}: prompt block names repeat: {sorted(repeated)}"
            )
        if bad := {
            name for name, seen_by in self.state_fields.items() if seen_by not in VISIBILITIES
        }:
            raise DeclarationRefused(
                f"agent {self.slug}: visibility is one of {sorted(VISIBILITIES)}: {sorted(bad)}"
            )
        pronunciations_checked(self.says, f"agent {self.slug}")
        for event, sources in self.events.items():
            if not event or not sources or sources - EVENT_SOURCES:
                raise DeclarationRefused(
                    f"agent {self.slug}: event {event!r} names who may send it: "
                    f"{sorted(EVENT_SOURCES)}"
                )

    def visibility_of(self, state_field: str) -> Visibility:
        """A state field is the tenant's unless declared otherwise; public and pii must be said."""
        return self.state_fields.get(state_field, "tenant")

    # The gateway asks this before an outside fact touches the log; undeclared is refused.
    def accepts(self, event: str, source: EventSource) -> bool:
        """Whether an outside event by this name, from this source, may reach the agent."""
        return source in self.events.get(event, frozenset())

    @property
    def tools_by_name(self) -> Mapping[str, ToolSpec]:
        """The declared tools, by the name the model calls."""
        return {tool.name: tool for tool in self.tools}


# One rule for a map of pronunciations, wherever one is declared: the class's own `says`, and the
# org's lexicon laid over it (types/tuning.py). `whose` names the declaration in the sentence.
def pronunciations_checked(says: Mapping[str, str], whose: str) -> None:
    """Refuse a word or a spoken form that is blank, naming whose declaration it was."""
    if blank := {word for word, spoken in says.items() if not word or not spoken}:
        raise DeclarationRefused(
            f"{whose}: a pronunciation is a word and how it is said: {sorted(blank)}"
        )
