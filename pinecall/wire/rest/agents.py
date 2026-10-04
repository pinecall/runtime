"""The bodies of the agent doors: agents held, sockets, the line, judging, pipeline, widget."""

from typing import Literal

from pinecall.domain.names import Channel, Env
from pinecall.wire.frames import WireModel
from pinecall.wire.parts import GreetingConfig, WidgetTheme
from pinecall.wire.rest.numbers import LegTrunk
from pinecall.wire.rest.providers import ProviderRow

# ── the agents an org holds, and whose terminal a ring lands in ──


class ScopeHolder(WireModel):
    """A scope by its member id and email; both null for the org's own."""

    holder: str | None
    name: str | None


class AgentRow(WireModel):
    """An agent some socket of the org holds, the doors it answers, and whose scope."""

    slug: str
    channels: list[Channel]
    holder: ScopeHolder | None = None


class AgentList(WireModel):
    """The org's held agents in the request's world."""

    agents: list[AgentRow]


class AppRow(WireModel):
    """One connected app socket and what it holds."""

    app: str
    agents: list[str]
    env: Env
    host: str | None
    address: str | None
    sdk: str | None
    holder: ScopeHolder | None
    connected_at: float


class AppList(WireModel):
    """The app sockets of the org this key may see."""

    apps: list[AppRow]


class StopAppResponse(WireModel):
    """A stop answered."""

    app: str
    stopped: bool


class RingTarget(WireModel):
    """Whose terminal a ring at the agent lands in, and who else could take it."""

    agent: str
    env: Env
    held: bool
    holding: ScopeHolder | None = None
    yours: bool
    waiting: list[ScopeHolder]
    calling: list[str]


class DeveloperPhones(WireModel):
    """The phone a developer calls from."""

    number: str


class TestNumber(WireModel):
    """A production number and the agent it reaches."""

    number: str
    agent: str


class TestNumbers(WireModel):
    """The person's own phones, and the production numbers they can dial to test."""

    calling: list[str]
    numbers: list[TestNumber]


class RingHandoff(WireModel):
    """Where a production ring goes: a developer's scope and its fleet, or nowhere (null)."""

    holder: str | None = None
    fleet: str | None = None
    # Where the sandbox has a LiveKit of its own: the ring is dialled to its SIP through this.
    trunk: LegTrunk | None = None


class JudgingSettings(WireModel):
    """Whether an org's calls are judged at hang-up, and the ceiling per call."""

    on: bool
    ceiling_usd: float | None


class JudgingRequest(WireModel):
    """JudgingSettings on or off."""

    on: bool


class HoldAudio(WireModel):
    """What a caller hears while a tool runs: the box's melody, silence, or the org's own clip."""

    played: Literal["default", "off", "custom"]
    sha256: str | None = None
    seconds: float | None = None
    name: str | None = None


class PlayedRequest(WireModel):
    """PUT /v1/agents/{slug}/pipeline/hold-audio/played: the box's melody back, or silence."""

    played: Literal["default", "off"]


class PipelineStage(WireModel):
    """One stage as the next call would run it: the vendor, its model, the voice or language."""

    vendor: str
    model: str | None = None
    voice_id: str | None = None
    language: str | None = None


class Measured(WireModel):
    """One latency over the agent's recent calls: livekit's name, the median, the turns."""

    name: str
    seconds: float
    turns: int


class PipelineReport(WireModel):
    """GET /v1/agents/{slug}/pipeline: what it hears, decides and speaks with, and how fast."""

    agent: str
    hears: PipelineStage
    decides: PipelineStage
    speaks: PipelineStage
    greeting: GreetingConfig | None
    voices: list[str]
    providers: list[ProviderRow]
    defaults: dict[str, str]
    models: dict[str, list[str]]
    calls: int
    medians: list[Measured]
    unavailable_reasons: dict[str, str]


class WidgetSettings(WireModel):
    """GET and PUT /v1/agents/{slug}/widget: how the widget presents the agent, per world."""

    title: str | None = None
    tagline: str | None = None
    greeting: str | None = None
    accent: str | None = None
    autostart: bool = False
    theme: WidgetTheme | None = None
