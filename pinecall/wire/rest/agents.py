"""The bodies of the agent doors: held agents, app sockets, the line, judging, the widget."""

from typing import Literal

from pinecall.domain.names import Channel, Env
from pinecall.wire.frames import WireModel

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
