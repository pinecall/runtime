"""The bodies of the hosting doors: hosted apps, releases, secrets, and the runner's report."""

from typing import Literal

from pydantic import Field

from pinecall.wire.frames import WireModel


class HostedAppRow(WireModel):
    """One app the box hosts for the org: its newest release, and how the runner left it."""

    name: str
    # The newest release, null before the first.
    release: int | None
    # The release serving the app now, null while none is.
    live_release: int | None
    # Why the newest release did not build or start, null when it did or is still on its way.
    failed_why: str | None
    created_by: str
    created_at: float


class HostedAppList(WireModel):
    """GET /v1/hosted: the apps the box hosts for the org in the world, by name."""

    apps: list[HostedAppRow]


class ReleaseRow(WireModel):
    """One release of a hosted app: its number, its sources' sha256 and size, who sent it."""

    name: str
    release: int
    sha256: str
    bytes: int
    author: str
    note: str
    created_at: float


class ReleaseList(WireModel):
    """GET /v1/hosted/{name}/releases: the app's releases, newest first."""

    releases: list[ReleaseRow]


class PutSecretRequest(WireModel):
    """PUT /v1/secrets/{name}, the body: the value, which no door ever answers back."""

    value: str


class SecretRow(WireModel):
    """One of the org's secrets as the list shows it: never its value."""

    name: str
    set_by: str
    set_at: float


class SecretList(WireModel):
    """GET /v1/secrets: the org's secrets in the world, by name."""

    secrets: list[SecretRow]


# ── the runner ──


class RunnerReport(WireModel):
    """What the runner says of one app: the host it answers for went live, or failed and why."""

    org: str
    name: str
    host: str
    state: Literal["live", "failed"]
    why: str = Field(default="", max_length=2000)


class RunnerHeartbeatRequest(WireModel):
    """POST /v1/runner/heartbeat: the runner's name, and what changed since its last."""

    runner: str
    reports: list[RunnerReport] = Field(default_factory=list[RunnerReport])


class WantedApp(WireModel):
    """One app the runner is to have running: a release, under the host name its process takes."""

    org: str
    name: str
    release: int
    sha256: str
    host: str
    # Whether an app socket of the org says it runs on that host: the release is serving.
    registered: bool
    # Whether this host was already reported failed: the runner leaves it alone.
    failed: bool


class RunnerHeartbeatResponse(WireModel):
    """The answer to a heartbeat: every app of the world that has a release."""

    apps: list[WantedApp]


class AppEnvironment(WireModel):
    """GET /v1/runner/apps/{org}/{name}/environment: what the app's process is started with."""

    environment: dict[str, str]
