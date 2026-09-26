"""The two projections of docs/protocol/projections.md: what leaves the platform, and to whom."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pinecall.log.pii import LEARNED_FROM, MASK
from pinecall.types.agent import AgentConfig, Visibility
from pinecall_protocol.defs import Projection

# The projection works on wire-shaped JSON, not on models: a sink sends bytes, and an entry's
# `data` is already a plain dict by the time it is stored. Nothing here validates — the entry was
# validated when it was written, and a whitelist that never constructs cannot invent a field.
type Json = dict[str, Any]

# The two projections and the three visibilities are named HERE and in tokens/scopes.py, nowhere
# else in the runtime: a sink that could spell "public" could also decide what it means.
PUBLIC: Projection = "public"
TENANT: Projection = "tenant"
PII: Visibility = "pii"

# Masked is one string, everywhere on the platform, and log/pii.py is where it is spelled: the
# masker writes it and this projection writes it, so a mask the two disagreed about would be two
# different holes in one log. It is re-exported because the contract's tests read it from here,
# which is the module docs/protocol/projections.md is about.


# ── the public whitelist, row by row of the contract ────────────────────────────

# The state fields public keeps, in the order the contract's table names them.
PUBLIC_STATE_FIELDS = (
    "seq",
    "status",
    "user_state",
    "agent_state",
    "live",
    "turns",
    "app_state",
    "room",
    "confirms",
    "transfer",
    "held",
    "events",
)

PUBLIC_TURN_FIELDS = frozenset({"role", "speech_id", "text", "interrupted"})
PUBLIC_PARTICIPANT_FIELDS = frozenset({"identity", "kind", "name", "joined_at", "speaking"})
PUBLIC_CONFIRM_FIELDS = ("phrase", "status")

# The one turn metric a participant may see: how long the platform took to answer them. Every
# other number is the tenant's cost and the tenant's tuning.
PUBLIC_TURN_METRIC = "e2e_latency"

# The envelope a public entry keeps. `agent` and `call` go the way the state's own `agent` and
# `call` go — absent — for the same reason: the participant asked about one call and learns
# nothing about which tenant's fleet answered it. `seq` is the cursor and `ts` is the clock a
# transcript is drawn on.
PUBLIC_ENVELOPE_FIELDS = ("seq", "ts", "type", "ephemeral")

# Every entry type public keeps, and the fields it keeps of it. A type absent from this table is
# dropped whole. Each row is named after the state row it feeds, which is why `call.line` keeps
# `held` and not `muted`, and why `confirm.granted` keeps nothing: the type IS the verdict.
PUBLIC_ENTRY_FIELDS: Mapping[str, tuple[str, ...]] = {
    # status
    "call.ringing": ("channel",),
    "call.dialing": ("channel",),
    "call.started": ("channel", "direction", "started_at"),
    "call.ended": ("reason", "ended_by", "ended_at", "duration_s"),
    # user_state, agent_state
    "user.state": ("state",),
    "agent.state": ("state",),
    # live: the interim words on screen, without the recognizer's opinion of them
    "user.transcript": ("text", "final"),
    "agent.transcript": ("speech_id", "text", "final"),
    # turns — turn.agent's metrics are filtered further, below
    "turn.user": ("speech_id", "text"),
    "turn.agent": ("speech_id", "text", "interrupted", "metrics"),
    # room
    "room.opened": ("name", "sid"),
    "participant.joined": ("identity", "kind", "name"),
    "participant.left": ("identity",),
    "participant.speaking": ("identity", "speaking"),
    # confirms
    "confirm.request": ("phrase", "ttl_s"),
    "confirm.granted": (),
    "confirm.declined": (),
    # transfer, held
    "call.transferred": ("to", "mode", "ok", "error"),
    "call.line": ("held",),
    # events: kept only when the viewer is the one who sent it
    "event.received": ("name", "data", "source", "identity"),
    # the cursor's own markers
    "log.gap": ("from_seq", "to_seq", "snapshot"),
    "log.caught_up": ("seq",),
    # app_state: filtered against the declaration, and never with its cause
    "state.changed": ("state", "changed"),
}


# ── the state ───────────────────────────────────────────────────────────────────


def project_state(
    state: Json,
    projection: Projection,
    declarations: AgentConfig | None = None,
    viewer: str | None = None,
) -> Json:
    """One reduced state as this projection lets it leave. Nothing else decides what it keeps."""
    if projection == TENANT:
        return {**state, "app_state": _masked(state["app_state"], declarations)}
    return {
        "seq": state["seq"],
        "status": state["status"],
        "user_state": state["user_state"],
        "agent_state": state["agent_state"],
        "live": state["live"],
        "turns": [_public_turn(turn) for turn in state["turns"]],
        "app_state": _only_public(state["app_state"], declarations),
        "room": _public_room(state["room"]),
        "confirms": [_kept(one, PUBLIC_CONFIRM_FIELDS) for one in state["confirms"]],
        "transfer": state["transfer"],
        "held": state["held"],
        "events": [one for one in state["events"] if _is_the_viewers(one, viewer)],
    }


def _public_turn(turn: Json) -> Json:
    """The words and who said them; of the agent's numbers, only how long the caller waited."""
    kept = _kept_set(turn, PUBLIC_TURN_FIELDS)
    if turn["role"] == "agent":
        kept["metrics"] = _public_metrics(turn["metrics"])
    return kept


def _public_metrics(metrics: Json) -> Json:
    """A turn's metrics, down to the one number that is about the caller's own wait."""
    return {name: value for name, value in metrics.items() if name == PUBLIC_TURN_METRIC}


def _public_room(room: Json | None) -> Json | None:
    """The room, with every participant's `attributes` gone: they carry the number and the trunk."""
    if room is None:
        return None
    seats = [_kept_set(one, PUBLIC_PARTICIPANT_FIELDS) for one in room["participants"]]
    return {**room, "participants": seats}


def _is_the_viewers(event: Json, viewer: str | None) -> bool:
    """An outside fact is the viewer's own when the viewer sent it; nobody else's is theirs."""
    return event.get("source") == "participant" and event.get("identity") == viewer


# ── one entry ───────────────────────────────────────────────────────────────────


def project_entry(
    entry: Json,
    projection: Projection,
    declarations: AgentConfig | None = None,
    viewer: str | None = None,
) -> Json | None:
    """One log entry as this projection lets it leave, or None when it never leaves at all."""
    if projection == TENANT:
        return {**entry, "data": _tenant_data(entry, declarations)}
    fields = PUBLIC_ENTRY_FIELDS.get(entry["type"])
    if fields is None:
        return None
    if entry["type"] == "event.received" and not _is_the_viewers(entry["data"], viewer):
        return None
    kept = _kept(entry, PUBLIC_ENVELOPE_FIELDS)
    kept["data"] = _public_data(entry["type"], _kept(entry["data"], fields), declarations, viewer)
    return kept


def _public_data(
    type: str, data: Json, declarations: AgentConfig | None, viewer: str | None
) -> Json:
    """The three payloads the whitelist alone cannot finish: a turn, a state change, a gap."""
    if type == "turn.agent" and "metrics" in data:
        return {**data, "metrics": _public_metrics(data["metrics"])}
    if type == "state.changed":
        return _public_state_change(data, declarations)
    if type == "log.gap" and data.get("snapshot") is not None:
        return {**data, "snapshot": project_state(data["snapshot"], PUBLIC, declarations, viewer)}
    return data


def _public_state_change(data: Json, declarations: AgentConfig | None) -> Json:
    """The app's state as it moved, down to the declared-public fields, and never why it moved."""
    state = _only_public(data.get("state", {}), declarations)
    return {
        "state": state,
        "changed": [name for name in data.get("changed", []) if name in state],
    }


def _tenant_data(entry: Json, declarations: AgentConfig | None) -> Json:
    """Everything, with the fields the app declared `pii` masked wherever a state travels."""
    data: Json = entry["data"]
    if entry["type"] in LEARNED_FROM:
        return {**data, "state": _masked(data.get("state", {}), declarations)}
    if entry["type"] == "log.gap" and data.get("snapshot") is not None:
        return {**data, "snapshot": project_state(data["snapshot"], TENANT, declarations)}
    return data


# ── the declaration ─────────────────────────────────────────────────────────────


def _only_public(app_state: Mapping[str, Any], declarations: AgentConfig | None) -> Json:
    """A field the app did not declare public is absent, not null: it cannot even be asked for."""
    return {
        name: value
        for name, value in app_state.items()
        if _visibility_of(name, declarations) == PUBLIC
    }


def _masked(app_state: Mapping[str, Any], declarations: AgentConfig | None) -> Json:
    """The tenant sees every field; a field it declared `pii` reads as the mask."""
    return {
        name: MASK if _visibility_of(name, declarations) == PII else value
        for name, value in app_state.items()
    }


def _visibility_of(state_field: str, declarations: AgentConfig | None) -> Visibility:
    """What the agent said about one field. An agent nobody has heard from declared nothing."""
    return TENANT if declarations is None else declarations.visibility_of(state_field)


# ── the whitelist itself ────────────────────────────────────────────────────────


def _kept(source: Mapping[str, Any], fields: Iterable[str]) -> Json:
    """The named fields that are actually there, in the whitelist's order. Nothing is invented."""
    return {name: source[name] for name in fields if name in source}


def _kept_set(source: Mapping[str, Any], fields: frozenset[str]) -> Json:
    """The same, for a whitelist whose order is the source's."""
    return {name: value for name, value in source.items() if name in fields}
