"""The bodies of the number doors: carrier accounts, numbers, dialling out, a leg's trunk."""

from typing import Literal

from pydantic import Field

from pinecall.domain.call import Route
from pinecall.domain.names import Channel, Env
from pinecall.wire.frames import WireModel
from pinecall.wire.parts import (
    Projection,
)

# ── dialling out ──


class DialResponse(WireModel):
    """POST /v1/agents/{slug}/dial, the answer."""

    call: str
    agent: str
    to: str
    from_: str = Field(alias="from")
    env: Env
    log_token: str


class DialGuards(WireModel):
    """GET /v1/carrier/outbound, the guards: what this org may dial and how often."""

    dial_anywhere: bool
    per_minute: int
    per_day: int
    max_duration_s: int


class OutboundStatus(WireModel):
    """GET /v1/carrier/outbound, the answer."""

    ready: bool
    kind: str | None
    from_numbers: list[str]
    steps_missing: list[str]
    guards: DialGuards


class ProvisionOutboundResponse(WireModel):
    """POST /v1/carrier/outbound, the answer: the plan, and whether it was carried out."""

    steps: list[str]
    dry_run: bool
    ready: bool
    trunk: str | None = None
    address: str | None = None


# ── numbers and the accounts they live in ──


class CarrierRow(WireModel):
    """GET /v1/carrier: one account of the org, named, never its secret."""

    kind: str
    account: str
    label: str = ""


class CarrierList(WireModel):
    """GET /v1/carriers: every account of the org, oldest first."""

    carriers: list[CarrierRow]


class OwnedNumberRow(WireModel):
    """A number an account owns, and whether this world imported it."""

    number: str
    name: str
    imported: bool
    account: str


class AvailableNumbers(WireModel):
    """GET /v1/numbers/available: what the org's Twilio accounts own; a peer lists none."""

    kind: str
    numbers: list[OwnedNumberRow]


class NumberRow(WireModel):
    """GET /v1/numbers, one row: a number of the org and the agent it reaches."""

    route: Route


class ImportNumberResponse(WireModel):
    """POST /v1/numbers and /v1/numbers/buy: the route, one sentence a step, whether it ran."""

    route: Route
    steps: list[str]
    dry_run: bool


class ImportNumberRequest(WireModel):
    """POST /v1/numbers: a number of one of the org's accounts, or one the org hooks itself."""

    number: str
    agent: str
    channel: Channel = "phone"
    account: str | None = None
    hooked: bool = False
    networks: list[str] = Field(default_factory=list[str])
    move: bool = False


class BuyNumberRequest(WireModel):
    """POST /v1/numbers/buy: a number wanted from the box's own account."""

    country: str = Field(min_length=2, max_length=2)
    area_code: str | None = None
    agent: str
    channel: Channel = "phone"


class MoveNumberRequest(WireModel):
    """PUT /v1/numbers/{number}/env: the world the number answers in from now on."""

    env: Env


class DialRequest(WireModel):
    """POST /v1/agents/{slug}/dial: the far end, and the org's number it is shown."""

    to: str
    from_: str | None = Field(None, alias="from")
    # What the log token handed back reads the call through.
    log: Projection = "tenant"


class LegTrunk(WireModel):
    """How the worker dials a leg: the host, its transport, the pair, the number shown."""

    hostname: str
    transport: Literal["auto", "udp", "tcp", "tls"]
    username: str
    password: str
    shown: str


class LegTrunkResponse(WireModel):
    """GET /v1/agents/{slug}/outbound-trunk: the leg's trunk, inline, after the guards."""

    trunk: LegTrunk
