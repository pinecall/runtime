"""What each reader receives: the filter it asked for, and the projection its credential allows."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.types import AgentConfig, Json, JsonObject
from pinecall.wire.events import TERMINAL_EVENT, AgentTurnEnded, LogGap, StateChanged, event_of
from pinecall.wire.frames import Entry, WireModel
from pinecall.wire.parts import Projection
from pinecall.wire.state import AgentTurn, State, Turn

MAX_TYPES = 32
_A_TYPE_NAME = re.compile(r"^[a-z0-9_.]+$")

# A reader that filtered these out could wait forever on a log that already ended.
ALWAYS_PASS = frozenset({"log.gap", "log.caught_up", "call.ended", TERMINAL_EVENT})

# The key stays, so a reader knows a value exists; the value goes whole, so its type does not leak.
MASK = "***"

# The entries whose data carries the app's state, masked for the tenant against the declaration.
CARRY_STATE = frozenset({"state.changed", "call.attached"})
NEEDS_READING = frozenset({"turn.agent", "state.changed", "log.gap"})

# The contract's table (docs/protocol/projections.md), row by row: a type absent is dropped
# whole, a field absent is dropped.
PUBLIC_ENTRY_FIELDS: Mapping[str, tuple[str, ...]] = {
    "call.ringing": ("channel",),
    "call.dialing": ("channel",),
    "call.started": ("channel", "direction", "started_at"),
    "call.ended": ("reason", "ended_by", "ended_at", "duration_s"),
    "user.state": ("state",),
    "agent.state": ("state",),
    "user.transcript": ("text", "final"),
    "agent.transcript": ("speech_id", "text", "final"),
    "turn.user": ("speech_id", "text"),
    "turn.agent": ("speech_id", "text", "interrupted", "metrics"),
    "room.opened": ("name", "sid"),
    "participant.joined": ("identity", "kind", "name"),
    "participant.left": ("identity",),
    "participant.speaking": ("identity", "speaking"),
    "confirm.request": ("phrase", "ttl_s"),
    "confirm.granted": (),
    "confirm.declined": (),
    "call.transferred": ("to", "mode", "ok", "error"),
    "call.line": ("held",),
    "event.received": ("name", "data", "source", "identity"),
    "state.changed": ("state", "changed"),
    "log.gap": ("from_seq", "to_seq", "snapshot"),
    "log.caught_up": ("seq",),
}

# agent and call are left out so a participant learns nothing of the tenant's setup.
PUBLIC_ENVELOPE = ("seq", "ts", "type", "ephemeral")
PUBLIC_TURN = frozenset({"role", "speech_id", "text", "interrupted"})
PUBLIC_SEAT = frozenset({"identity", "kind", "name", "joined_at", "speaking"})
PUBLIC_CONFIRM = frozenset({"phrase", "status"})


@dataclass(frozen=True, slots=True)
class Filter:
    """One reader's filter: the types it wants (None for every one), and whether only durable."""

    types: frozenset[str] | None = None
    durable: bool = False

    def passes(self, entry: Entry) -> bool:
        """Return whether the entry reaches this reader."""
        if entry.type in ALWAYS_PASS:
            return True
        if self.durable and entry.ephemeral:
            return False
        return self.types is None or entry.type in self.types


EVERYTHING = Filter()


def parse_filter(types: str | None, *, durable: bool) -> Filter:
    """Return the filter a query string asks for (`types=a.b,c.d`), refusing a bad type name."""
    if types is None:
        return Filter(durable=durable)
    wanted = [name.strip() for name in types.split(",") if name.strip()]
    if len(wanted) > MAX_TYPES:
        raise DeclarationRefused(f"a filter names at most {MAX_TYPES} types, not {len(wanted)}")
    if bad := [name for name in wanted if not _A_TYPE_NAME.match(name)]:
        raise DeclarationRefused(f"an event type is lowercase words joined by dots: {bad}")
    return Filter(types=frozenset(wanted), durable=durable)


def project_state(
    state: State, projection: Projection, config: AgentConfig | None, viewer: str | None = None
) -> JsonObject:
    """Return the reduced state as this audience may read it."""
    if projection == "tenant":
        return {**state.written(), "app_state": _masked(state.app_state, config)}
    written = state.written()
    room: Json = None
    if state.room is not None:
        seats: list[Json] = [_kept(seat.written(), PUBLIC_SEAT) for seat in state.room.participants]
        room = {**state.room.written(), "participants": seats}
    return {
        "seq": state.seq,
        "status": state.status,
        "user_state": written["user_state"],
        "agent_state": written["agent_state"],
        "live": written["live"],
        "turns": [_public_turn(turn) for turn in state.turns],
        "app_state": _only_public(state.app_state, config),
        "room": room,
        "confirms": [_kept(one.written(), PUBLIC_CONFIRM) for one in state.confirms],
        "transfer": written["transfer"],
        "held": state.held,
        "events": [
            one.written()
            for one in state.events
            if one.source == "participant" and one.identity == viewer
        ],
    }


def project_entry(
    entry: Entry, projection: Projection, config: AgentConfig | None, viewer: str | None = None
) -> JsonObject | None:
    """Return the entry as this audience may read it, or None when it must not reach them."""
    if projection == "tenant":
        return {**entry.written(), "data": _tenant_data(entry, config)}
    fields = PUBLIC_ENTRY_FIELDS.get(entry.type)
    if fields is None:
        return None
    data: JsonObject = {name: entry.data[name] for name in fields if name in entry.data}
    if entry.type == "event.received" and not (
        data.get("source") == "participant" and data.get("identity") == viewer
    ):
        return None
    match _readable(entry):
        case AgentTurnEnded() as turn:
            data["metrics"] = _public_metrics(turn.metrics.e2e_latency)
        case StateChanged() as changed:
            public = _only_public(changed.state, config)
            kept: list[Json] = [name for name in changed.changed if name in public]
            data = {"state": public, "changed": kept}
        case LogGap(snapshot=State() as snapshot):
            data["snapshot"] = project_state(snapshot, "public", config, viewer)
        case None if entry.type in NEEDS_READING:
            return None
        case _:
            pass
    envelope = entry.written()
    return {**{name: envelope[name] for name in PUBLIC_ENVELOPE}, "data": data}


def _tenant_data(entry: Entry, config: AgentConfig | None) -> JsonObject:
    state = entry.data.get("state")
    if entry.type in CARRY_STATE and isinstance(state, dict):
        return {**entry.data, "state": _masked(state, config)}
    gap = _readable(entry)
    if isinstance(gap, LogGap) and gap.snapshot is not None:
        return {**entry.data, "snapshot": project_state(gap.snapshot, "tenant", config)}
    return entry.data


# Only the entries a projection reaches into are read; an old shape of one is withheld from the
# public, since what it would leak cannot be told, and passed whole to the tenant.
def _readable(entry: Entry) -> WireModel | None:
    if entry.type not in NEEDS_READING:
        return None
    try:
        return event_of(entry)
    except DeclarationRefused:
        return None


def _public_turn(turn: Turn) -> JsonObject:
    kept = _kept(turn.written(), PUBLIC_TURN)
    if isinstance(turn, AgentTurn):
        kept["metrics"] = _public_metrics(turn.metrics.e2e_latency)
    return kept


# How long the caller waited is the one number about them; the rest is the tenant's cost and tuning.
def _public_metrics(e2e_latency: float | None) -> JsonObject:
    return {} if e2e_latency is None else {"e2e_latency": e2e_latency}


# Without a declaration every field is the tenant's: nothing is public, nothing is masked.
def _only_public(app_state: JsonObject, config: AgentConfig | None) -> JsonObject:
    if config is None:
        return {}
    return {
        name: value for name, value in app_state.items() if config.visibility_of(name) == "public"
    }


def _masked(app_state: JsonObject, config: AgentConfig | None) -> JsonObject:
    if config is None:
        return dict(app_state)
    return {
        name: MASK if config.visibility_of(name) == "pii" else value
        for name, value in app_state.items()
    }


def _kept(written: JsonObject, fields: frozenset[str]) -> dict[str, Json]:
    return {name: value for name, value in written.items() if name in fields}
