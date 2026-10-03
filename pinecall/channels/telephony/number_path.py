"""What a call to a number goes through now: its carrier, the fence, its world's rule, its agent."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from pinecall.channels.routes import RouteRecord
from pinecall.channels.telephony import sip
from pinecall.channels.telephony._twilio import origination_uri
from pinecall.channels.telephony.carrier import control_of, declared_networks, own_networks
from pinecall.channels.telephony.carrier_catalog import BOX_CARRIER, known
from pinecall.domain.errors import PinecallError
from pinecall.domain.names import StepState
from pinecall.process.connections import Connections
from pinecall.tenancy import carrier_networks
from pinecall.tenancy.carriers import Carrier, carriers_of

type Step = Literal["carrier", "fence", "world", "agent"]


# A state ranked by how much it stops a call: the worst step is what the number does.
RANK: dict[StepState, int] = {"ok": 0, "waiting": 1, "broken": 2}


@dataclass(frozen=True)
class PathStep:
    """One step of a call's way to its agent, what it does now, and what would make it work."""

    step: Step
    state: StepState
    says: str
    fix: str | None = None


@dataclass(frozen=True)
class NumberPath:
    """A number's four steps, and the worst of them: what a call to it does now."""

    steps: list[PathStep]
    rings: StepState


async def path_of(connections: Connections, record: RouteRecord, *, running: bool) -> NumberPath:
    """The number's four steps, its carrier's API asked when it has one."""
    carrier, approved = await _what_fences(connections, record.route.org)
    steps = [
        await _carrier_step_asked(connections, record, carrier.get(record.account or "")),
        _fence_step(record, carrier.get(record.account or ""), approved),
        _world_step(record),
        _agent_step(record, running=running),
    ]
    return NumberPath(steps=steps, rings=worst(step.state for step in steps))


async def rings_of(
    connections: Connections, records: list[RouteRecord], running: Callable[[RouteRecord], bool]
) -> list[StepState]:
    """What a call to each of one org's numbers does now, read off the tables, no carrier asked."""
    if not records:
        return []
    carriers, approved = await _what_fences(connections, records[0].route.org)
    return [
        worst(
            step.state
            for step in (
                _carrier_step(record, pointed_by_the_box=_pointed(connections, record, carriers)),
                _fence_step(record, carriers.get(record.account or ""), approved),
                _agent_step(record, running=running(record)),
            )
        )
        for record in records
    ]


def worst(states: Iterable[StepState]) -> StepState:
    """The state that stops a call most, of several."""
    found: StepState = "ok"
    for state in states:
        if RANK[state] > RANK[found]:
            found = state
    return found


async def _what_fences(
    connections: Connections, org: str
) -> tuple[dict[str, Carrier], dict[str, tuple[str, ...]]]:
    carriers = await carriers_of(connections.pool, connections.vault, org)
    approved = await carrier_networks.approved(connections.pool, org)
    return {carrier.id: carrier for carrier in carriers}, approved


# A number the box pointed here through an API is taken as pointed until a look says otherwise.
def _carrier_step(record: RouteRecord, *, pointed_by_the_box: bool) -> PathStep:
    if record.last_call_at is not None:
        return PathStep("carrier", "ok", "Calls to it reach this box")
    if record.route.channel == "whatsapp":
        return PathStep("carrier", "waiting", "Waiting for the first message")
    if pointed_by_the_box:
        return PathStep("carrier", "ok", "Pointed at this box when it was added")
    return PathStep("carrier", "waiting", f"Waiting for the first call to {record.route.number}")


def _pointed(connections: Connections, record: RouteRecord, carriers: dict[str, Carrier]) -> bool:
    carrier = carriers.get(record.account or "")
    return record.route.managed or (
        carrier is not None and control_of(connections.http, carrier) is not None
    )


async def _carrier_step_asked(
    connections: Connections, record: RouteRecord, carrier: Carrier | None
) -> PathStep:
    control = None if carrier is None else control_of(connections.http, carrier)
    if record.last_call_at is not None or control is None or record.route.channel != "phone":
        return _carrier_step(record, pointed_by_the_box=record.route.managed)
    number = str(record.route.number)
    here = origination_uri(sip.domain_of(connections, record.route.env))
    try:
        owned = await control.number(number)
        trunk = await control.trunk_pointing_at(here)
    except PinecallError:
        return PathStep("carrier", "waiting", "Twilio did not answer: it is asked again next time")
    if owned is not None and trunk is not None and owned.trunk_sid == trunk.sid:
        return PathStep("carrier", "ok", "Twilio sends it to this box")
    return PathStep(
        "carrier",
        "broken",
        "Twilio does not send it to this box",
        fix="Add it again from your Twilio account: it is pointed here on the way",
    )


def _fence_step(
    record: RouteRecord, carrier: Carrier | None, approved: dict[str, tuple[str, ...]]
) -> PathStep:
    if record.route.channel == "whatsapp":
        return PathStep("fence", "ok", "Signed by Meta, checked on every message")
    if record.answering != record.route.org:
        return PathStep(
            "fence",
            "broken",
            "Another org's older row answers this number",
            fix="Ask the box's operator to free it",
        )
    number = str(record.route.number)
    declared = (record.networks or None) if carrier is None else declared_networks(carrier)
    source = number if carrier is None else carrier.id
    own, waiting = own_networks(declared, approved.get(source, ()))
    fence = sip.fence_now(record, carrier, approved)
    if fence is None:
        return PathStep(
            "fence",
            "waiting",
            f"{', '.join(waiting)} waits for the box's operator",
            fix="The operator approves it once under Carriers",
        )
    kind = record.via or BOX_CARRIER
    who = known()[kind].name if own is None and kind in known() else "your PBX"
    return PathStep("fence", "ok", f"Admitted from {who}'s networks")


def _world_step(record: RouteRecord) -> PathStep:
    env = record.route.env
    return PathStep("world", "ok", f"The {env} rule sends it to the {env} fleet")


def _agent_step(record: RouteRecord, *, running: bool) -> PathStep:
    agent, env = record.route.agent, record.route.env
    if running:
        return PathStep("agent", "ok", f"{agent} is running")
    return PathStep(
        "agent",
        "broken",
        f"Nobody runs {agent} in the {env}",
        fix="Run pinecall start in the agent's folder, or deploy it",
    )
