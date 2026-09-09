"""AgentConfig: everything an app declares about one agent, whole and frozen, for both processes."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, get_args

from pinecall.types.channel import CHANNELS, Channel
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


@dataclass(frozen=True)
class Turn:
    """How the session decides the caller is done, and how much it takes to interrupt the agent."""

    min_interruption_words: int | None = None
    endpointing_ms: int | None = None


# Field names agree with the wire's AgentConfig, so the gateway maps one onto the other by name;
# tests/types/test_wire_agreement.py holds them to it.
@dataclass(frozen=True)
class AgentConfig:
    """What an app declared about one agent, resolved: the runtime reads this and asks no more."""

    slug: str
    channels: frozenset[Channel] = frozenset()
    name: str | None = None
    prompt: tuple[PromptBlock, ...] = DEFAULT_LAYOUT
    greeting: str | None = None
    language: str | None = None
    voice: Voice | None = None
    llm: Model | None = None
    stt: Model | None = None
    turn: Turn | None = None
    says: Mapping[str, str] = field(default_factory=dict[str, str])
    hears: tuple[str, ...] = ()
    knowledge: str | None = None
    docs: Docs | None = None
    memory: MemoryPolicy | None = None
    tools: tuple[ToolSpec, ...] = ()
    state_fields: Mapping[str, Visibility] = field(default_factory=dict[str, Visibility])
    events: Mapping[str, frozenset[EventSource]] = field(
        default_factory=dict[str, frozenset[EventSource]]
    )

    def __post_init__(self) -> None:
        if not _A_SLUG.match(self.slug):
            raise DeclarationRefused(
                f"an agent's slug is lowercase words joined by dashes, not {self.slug!r}"
            )
        if unknown := self.channels - CHANNELS:
            raise DeclarationRefused(f"agent {self.slug}: unknown channels {sorted(unknown)}")
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
        if blank := {word for word, spoken in self.says.items() if not word or not spoken}:
            raise DeclarationRefused(
                f"agent {self.slug}: a pronunciation is a word and how it is said: {sorted(blank)}"
            )
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
