"""One row per call: what a list filters on and a day is counted from, folded entry by entry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

from pinecall.log.entry import Entry

# The judge whose broken verdict is the `promise` flag (evals/judges/promises.py). Spelled here
# because log/ imports no evals/: the flag is read off the entry's own words, as every fact is.
PROMISES = "promises"

# The two answers a judge gives about a call. `skipped` and `deferred` are rows for a judge that
# did not settle it, so neither is counted as judged.
SETTLED = frozenset({"held", "broken"})

# How a call ended that says a person took it from the agent.
ENDED_BY_A_PERSON = frozenset({"transferred", "supervisor_ended"})

# The entries that say a person took part: the line taken, words said as the agent, a transfer, an
# end. A whisper is advice to the agent and leaves the conversation the agent's.
A_PERSON_TOOK_PART = frozenset(
    {
        "supervisor.took_over",
        "supervisor.said",
        "supervisor.ended",
        "supervisor.transferred",
        "call.transferred",
    }
)

# The one channel that is always spoken. A web call is spoken when a room opened for it.
SPOKEN_CHANNEL = "phone"


# A call's facts are a PROJECTION of its log, kept beside it so a list, a day and an inbox are one
# indexed read and never a fold of every log. The log is still the truth: every field here is what
# some entry said, and a store that lost the row could fold it back (0026 does, for what came
# before). Nothing in it is ever the only place a fact lives.
@dataclass(frozen=True)
class CallFacts:
    """What the list, the day and the inbox read about one call, without opening its log."""

    call: str
    agent: str = ""
    channel: str | None = None
    direction: str | None = None
    from_: str | None = None
    to: str | None = None
    name: str | None = None
    # Who the inbox files the call under: the id the app resolved, else the number or visitor id.
    contact: str | None = None
    spoken: bool = False
    ended_at: float | None = None
    end_reason: str | None = None
    outcome: str | None = None
    cost_eur: float | None = None
    judged: int | None = None
    held: int | None = None
    passed: bool | None = None
    reason: str | None = None
    escalated: bool = False
    promised: bool = False
    # Every agent turn's e2e_latency, and when each caller turn landed: a median, an unread count.
    e2e: tuple[float, ...] = ()
    heard_at: tuple[float, ...] = ()
    last_text: str | None = None
    last_at: float | None = None
    last_in: bool | None = None

    def changed(self, change: Change) -> CallFacts:
        """These facts with what one entry said laid over them, as the postgres upsert lays it."""
        # A verdict replaces the last one whole: a call judged again is what the new judges said.
        verdict = change if change.scored else self
        said = change.last_at is not None
        return replace(
            self,
            channel=change.channel or self.channel,
            direction=change.direction or self.direction,
            from_=change.from_ or self.from_,
            to=change.to or self.to,
            name=change.name or self.name,
            contact=change.contact or self.contact,
            spoken=self.spoken or change.spoken,
            ended_at=change.ended_at if change.ended_at is not None else self.ended_at,
            end_reason=self.end_reason or change.end_reason,
            outcome=change.outcome if change.outcome is not None else self.outcome,
            cost_eur=change.cost_eur if change.cost_eur is not None else self.cost_eur,
            escalated=self.escalated or change.escalated,
            e2e=(*self.e2e, *change.e2e),
            heard_at=(*self.heard_at, *change.heard_at),
            last_text=change.last_text if said else self.last_text,
            last_at=change.last_at if said else self.last_at,
            last_in=change.last_in if said else self.last_in,
            judged=verdict.judged,
            held=verdict.held,
            passed=verdict.passed,
            reason=verdict.reason,
            promised=verdict.promised,
        )

    @property
    def score_row(self) -> dict[str, Any] | None:
        """The SessionScore a list draws, or None when no judge settled anything about the call."""
        if not self.judged:
            return None
        return {
            "held": self.held or 0,
            "judged": self.judged,
            "passed": self.passed is not False,
            "reason": self.reason,
        }

    @property
    def flags(self) -> list[str]:
        """What a person reviewing calls looks at first, in the order the protocol lists them."""
        raised = [
            ("escalated", self.escalated),
            ("low_score", self.passed is False),
            ("promise", self.promised),
        ]
        return [flag for flag, up in raised if up]


@dataclass(frozen=True)
class Change:
    """What one entry says about its call. None says nothing; `scored` overwrites the verdict."""

    channel: str | None = None
    direction: str | None = None
    from_: str | None = None
    to: str | None = None
    name: str | None = None
    contact: str | None = None
    spoken: bool = False
    ended_at: float | None = None
    end_reason: str | None = None
    outcome: str | None = None
    cost_eur: float | None = None
    scored: bool = False
    judged: int | None = None
    held: int | None = None
    passed: bool | None = None
    reason: str | None = None
    promised: bool = False
    escalated: bool = False
    e2e: tuple[float, ...] = ()
    heard_at: tuple[float, ...] = ()
    last_text: str | None = None
    last_at: float | None = None
    last_in: bool | None = None


def change_of(entry: Entry) -> Change | None:
    """What this entry says about its call's facts, or None when it says nothing about them."""
    if entry.call is None or entry.ephemeral:
        return None
    if entry.type in A_PERSON_TOOK_PART:
        return Change(escalated=True)
    reading = READERS.get(entry.type)
    return None if reading is None else reading(entry)


def _the_line(entry: Entry) -> Change:
    """ringing, dialing, started: the door, the two sides, and who the contact is."""
    data = entry.data
    caller = _a_mapping(data.get("caller"))
    channel = _a_word(data.get("channel"))
    direction = _a_word(data.get("direction")) or (
        "outbound" if entry.type == "call.dialing" else "inbound"
    )
    return Change(
        channel=channel,
        direction=direction,
        from_=_a_word(data.get("from")),
        to=_a_word(data.get("to")),
        name=_a_word(caller.get("name")),
        contact=_a_word(caller.get("id")) or _a_word(data.get("from")),
        spoken=channel == SPOKEN_CHANNEL,
    )


def _ended(entry: Entry) -> Change:
    reason = _a_word(entry.data.get("reason"))
    return Change(
        ended_at=_a_number(entry.data.get("ended_at")) or entry.ts,
        end_reason=reason,
        escalated=reason in ENDED_BY_A_PERSON,
    )


def _summed_up(entry: Entry) -> Change:
    cost = _a_mapping(entry.data.get("cost"))
    return Change(
        outcome=_a_word(entry.data.get("outcome")),
        cost_eur=_a_number(cost.get("eur")),
        end_reason=_a_word(entry.data.get("reason")),
    )


def _scored(entry: Entry) -> Change:
    """call.score: how many judges settled it, how many held, and why the first one broke."""
    judges = [_a_mapping(one) for one in _a_list(entry.data.get("judges"))]
    settled = [one for one in judges if one.get("verdict") in SETTLED]
    broken = [one for one in settled if one.get("verdict") == "broken"]
    passed = entry.data.get("passed")
    return Change(
        scored=True,
        judged=len(settled),
        held=len(settled) - len(broken),
        passed=passed if isinstance(passed, bool) else (None if not settled else not broken),
        reason=_a_word(broken[0].get("reason")) if broken else None,
        promised=any(one.get("name") == PROMISES for one in broken),
    )


def _heard(entry: Entry) -> Change:
    return Change(
        heard_at=(entry.ts,),
        last_text=_a_word(entry.data.get("text")),
        last_at=entry.ts,
        last_in=True,
    )


def _answered(entry: Entry) -> Change:
    metrics = _a_mapping(entry.data.get("metrics"))
    e2e = _a_number(metrics.get("e2e_latency"))
    return Change(
        e2e=() if e2e is None else (e2e,),
        last_text=_a_word(entry.data.get("text")),
        last_at=entry.ts,
        last_in=False,
    )


READERS: Mapping[str, Callable[[Entry], Change]] = {
    "call.ringing": _the_line,
    "call.dialing": _the_line,
    "call.started": _the_line,
    "call.ended": _ended,
    "call.summary": _summed_up,
    "call.score": _scored,
    "room.opened": lambda _: Change(spoken=True),
    "turn.user": _heard,
    "turn.agent": _answered,
}


# The log outlives the shape of what is in it, so every field is read for what it is and a value
# of another shape is nothing rather than a refusal: a row that cannot be read says less, it never
# stops the append that wrote it.
def _a_word(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _a_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _a_mapping(value: Any) -> Mapping[str, Any]:
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else {}


def _a_list(value: Any) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []
