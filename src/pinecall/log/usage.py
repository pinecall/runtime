"""Usage: the facts an org consumed, folded from the log's own call.summary and call.score rows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from pinecall.log.store.protocol import Metered

# The two entries that carry what a call consumed: the summary the session wrote at hang-up, with
# every model's usage rows, and the score, with how many questions the judges put to a model.
# Nothing else in the log is metered, and a usage row is never a table of its own: this module is
# a projection, and the log it reads is the truth. See docs/decisions/orgs.md.
METERED_TYPES: tuple[str, ...] = ("call.summary", "call.score")

# A log written before the org column existed, or one nobody claimed, is nobody's in the store;
# the projection files it under this word rather than dropping the facts on the floor.
UNOWNED = "unowned"


@dataclass(frozen=True, slots=True)
class UsageRow:
    """One metered entry as the operator reads it: whose call, and what it consumed."""

    cursor: int
    org: str
    agent: str
    call: str
    type: str
    at: float
    minutes: float = 0.0
    messages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    characters: int = 0
    judge_calls: int = 0
    # Informational: the provider's bill as best the log knows it. Never a price of ours.
    cost_eur: float = 0.0


@dataclass(frozen=True, slots=True)
class Totals:
    """What one org has consumed so far, summed over every row read."""

    minutes: float = 0.0
    messages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    characters: int = 0
    judge_calls: int = 0
    cost_eur: float = 0.0
    calls: int = 0

    def plus(self, row: UsageRow) -> Totals:
        """These totals with one more row folded in. A score row adds no call: its summary did."""
        return replace(
            self,
            minutes=self.minutes + row.minutes,
            messages=self.messages + row.messages,
            input_tokens=self.input_tokens + row.input_tokens,
            output_tokens=self.output_tokens + row.output_tokens,
            characters=self.characters + row.characters,
            judge_calls=self.judge_calls + row.judge_calls,
            cost_eur=self.cost_eur + row.cost_eur,
            calls=self.calls + (1 if row.type == "call.summary" else 0),
        )


def a_usage_row(metered: Metered) -> UsageRow:
    """One metered entry folded: the summary's minutes, turns and model rows, or the score's."""
    entry = metered.entry
    row = UsageRow(
        cursor=metered.position,
        org=metered.org or UNOWNED,
        agent=entry.agent,
        call=entry.call or "",
        type=entry.type,
        at=entry.ts,
    )
    if entry.type == "call.summary":
        return _from_a_summary(row, entry.data)
    return _from_a_score(row, entry.data)


def totals_by_org(rows: Iterable[UsageRow]) -> dict[str, Totals]:
    """Every org's totals over these rows, in the order the orgs first appear."""
    folded: dict[str, Totals] = {}
    for row in rows:
        folded[row.org] = folded.get(row.org, Totals()).plus(row)
    return folded


# The summary's usage rows are livekit's own, one per model, told apart by their type tag: an LLM
# row counts tokens, a TTS row characters. Every other tag — STT, EOT, interruption — carries audio
# seconds the summary's own duration already covers.
def _from_a_summary(row: UsageRow, data: Mapping[str, Any]) -> UsageRow:
    """Minutes and turns off the summary, tokens and characters off its model rows."""
    input_tokens = output_tokens = characters = 0
    for used in data.get("usage", ()):
        tag = used.get("type")
        if tag == "llm":
            input_tokens += int(used.get("input_tokens", 0))
            output_tokens += int(used.get("output_tokens", 0))
        elif tag == "tts":
            characters += int(used.get("characters_count", 0))
    return replace(
        row,
        minutes=float(data.get("duration_s", 0.0)) / 60.0,
        messages=int(data.get("turns", 0)),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        characters=characters,
        cost_eur=float(data.get("cost", {}).get("eur", 0.0)),
    )


def _from_a_score(row: UsageRow, data: Mapping[str, Any]) -> UsageRow:
    """How many questions the judges asked a model, and what the log says they cost."""
    return replace(
        row,
        judge_calls=int(_measured(data, "judge_calls")),
        cost_eur=_measured(data, "judge_cost_eur"),
    )


# A key that is PRESENT and null is not a key that is missing, and `.get(name, 0)` only answers the
# second one. `CallScore.judge_cost_eur` is `float | None` on purpose — a call nobody judged cost
# nothing to judge — and it serialises as null, not as absent. So every score of an unjudged call,
# which is most of them, reached `float(None)` and took the whole Usage page down with a 500.
def _measured(data: Mapping[str, Any], name: str) -> float:
    """The number the log recorded under that name; nothing recorded and nothing measured are 0."""
    value = data.get(name)
    return 0.0 if value is None else float(value)
