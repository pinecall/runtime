"""The bodies of the hosting doors: the apps the box hosts, their releases, the org's secrets."""

from pinecall.wire.frames import WireModel


class HostedAppRow(WireModel):
    """One app the box hosts for the org: its newest release, null before the first."""

    name: str
    release: int | None
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
