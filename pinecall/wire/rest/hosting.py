"""The bodies of the hosting doors: hosted apps, releases, secrets, and the runner's report."""

from typing import Literal

from pydantic import Field

from pinecall.domain.names import Env
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
    # A person stopped it: nothing runs, and its releases and token stay.
    stopped: bool
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


class RollbackRequest(WireModel):
    """POST /v1/hosted/{name}/rollback: which release's sources go again, as the next release."""

    release: int = Field(ge=1)


class AppLogsResponse(WireModel):
    """GET /v1/hosted/{name}/logs: the last lines the runner read, and which process, and when."""

    name: str
    host: str | None
    lines: str
    # Null before the runner has sent any: asking is what makes it send them.
    at: float | None


class ServedRow(WireModel):
    """The time one app served on one UTC day."""

    org: str
    env: Env
    name: str
    day: str
    seconds: float


class ServedPage(WireModel):
    """GET /v1/hosted/usage and GET /v1/ops/hosted-usage: the time apps served, per day."""

    since: str
    until: str
    rows: list[ServedRow]


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


class AppLogs(WireModel):
    """The last lines of one app's process, as the runner read them from its container."""

    org: str
    name: str
    host: str
    lines: str = Field(max_length=256 * 1024)


class RunnerHeartbeatRequest(WireModel):
    """POST /v1/runner/heartbeat: the runner's name, and what changed since its last."""

    runner: str
    reports: list[RunnerReport] = Field(default_factory=list[RunnerReport])
    logs: list[AppLogs] = Field(default_factory=list[AppLogs])


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
    # Whether a person asked for its logs lately: the runner sends them with its next beat.
    logs_wanted: bool = False


class RunnerHeartbeatResponse(WireModel):
    """The answer to a heartbeat: the runner's world, and every app of it that has a release."""

    world: Env
    apps: list[WantedApp]


class AppEnvironment(WireModel):
    """GET /v1/runner/apps/{org}/{name}/environment: what the app's process is started with."""

    environment: dict[str, str]
