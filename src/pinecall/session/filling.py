"""What a session asks of the platform around a turn: its markers' fills, and memory at hang-up."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Protocol

from pinecall.types import Blocks, KnowledgeFile, Marker, markers_in
from pinecall_protocol.events import ErrorEvent


class Filler(Protocol):
    """Who answers a turn's markers: the gateway in-process on text, over HTTP on voice."""

    async def fill(
        self, call: str, query: str, markers: Sequence[Marker], speech_id: str | None
    ) -> Mapping[str, str]:
        """The text each marker line becomes for this turn, keyed by the line as written."""
        ...


class Rememberer(Protocol):
    """Who writes what a call taught about the contact, once, at hang-up."""

    async def remember(self, call: str) -> None:
        """Read the call's turns off its log and write the memory ops."""
        ...


class NoFiller:
    """A process with nothing to fill from: every marker becomes nothing."""

    async def fill(
        self,
        call: str,  # noqa: ARG002 — the protocol's shape
        query: str,  # noqa: ARG002 — the protocol's shape
        markers: Sequence[Marker],  # noqa: ARG002 — the protocol's shape
        speech_id: str | None,  # noqa: ARG002 — the protocol's shape
    ) -> Mapping[str, str]:
        """Nothing, for every marker."""
        return {}


class NoRememberer:
    """A process that keeps no memory: a hang-up writes nothing."""

    async def remember(self, call: str) -> None:  # noqa: ARG002 — the protocol's shape
        """Nothing."""
        return None


# The codes the log carries when the platform did not answer in time, by what was asked.
SKIPPED: dict[str, str] = {"memory": "memory_skipped", "retrieved": "retrieval_skipped"}
REMEMBER_FAILED = "remember_failed"


# One per call. The knowledge fill is the file's text, fixed when the session starts, wherever
# its marker is written — so the static prefix is the same bytes on every request. The turn's
# fills are replaced whole when the caller's turn ends, and hold nothing between two turns.
class Filling:
    """One call's fills: the knowledge, fixed; the turn's memory and retrieval, under a budget."""

    def __init__(
        self,
        filler: Filler,
        call: str,
        blocks: Blocks,
        knowledge: KnowledgeFile | None,
        budget_ms: int,
    ) -> None:
        self._filler = filler
        self._call = call
        self._blocks = blocks
        self._knowledge = knowledge.text if knowledge is not None else ""
        self._budget_ms = budget_ms
        self._turn: Mapping[str, str] = {}

    @property
    def fills(self) -> Mapping[str, str]:
        """Every fill of the request about to go out: the knowledge, then this turn's."""
        return {**self._the_knowledge(), **self._turn}

    # The whole caller turn is the query; a later card may ask on the eager partial transcript.
    # A marker never delays a reply past the budget: past it, or on any failure, the turn goes on
    # with nothing filled and the log says which fill was skipped and why.
    async def turn_ended(self, query: str, speech_id: str | None) -> tuple[ErrorEvent, ...]:
        """This turn's fills, asked with the caller's words; what went unfilled, as entries."""
        self._turn = {}
        markers = self._the_turns_markers()
        if not markers:
            return ()
        try:
            answered = await asyncio.wait_for(
                self._filler.fill(self._call, query, markers, speech_id), self._budget_ms / 1000
            )
        except TimeoutError:
            return _skipped(markers, f"no answer within {self._budget_ms} ms")
        except Exception as failed:  # noqa: BLE001 — a fill must never break a reply
            return _skipped(markers, str(failed) or type(failed).__name__)
        self._turn = dict(answered)
        return ()

    def _the_knowledge(self) -> dict[str, str]:
        """The knowledge marker's line, wherever it is written, to the file's text."""
        if not self._knowledge:
            return {}
        texts = (*self._blocks.static_texts, *self._blocks.dynamic_texts)
        return {
            marker.line: self._knowledge
            for text in texts
            for marker in markers_in(text)
            if marker.name == "knowledge"
        }

    def _the_turns_markers(self) -> tuple[Marker, ...]:
        """The memory and retrieved markers of the dynamic blocks, in the order written."""
        return tuple(
            marker
            for text in self._blocks.dynamic_texts
            for marker in markers_in(text)
            if marker.name in SKIPPED
        )


# Between call.ended and call.summary, so every turn is in the log when memory reads it back, and
# inside a budget, so a slow model at hang-up never holds the seal: the call seals either way.
async def remembered_within(
    rememberer: Rememberer, call: str, budget_s: float
) -> ErrorEvent | None:
    """remember(call) inside its budget; what went wrong as the entry to write, or None."""
    try:
        await asyncio.wait_for(rememberer.remember(call), budget_s)
    except TimeoutError:
        return _failed_to_remember(f"no answer within {budget_s:g} s")
    except Exception as failed:  # noqa: BLE001 — the call seals whatever memory did
        return _failed_to_remember(str(failed) or type(failed).__name__)
    return None


def _skipped(markers: Sequence[Marker], why: str) -> tuple[ErrorEvent, ...]:
    """One recoverable error per kind of marker that went unfilled, in a fixed order."""
    codes = sorted({SKIPPED[marker.name] for marker in markers})
    return tuple(
        ErrorEvent(
            code=code,
            message=f"{code.removesuffix('_skipped')} was not filled: {why}",
            recoverable=True,
        )
        for code in codes
    )


def _failed_to_remember(why: str) -> ErrorEvent:
    return ErrorEvent(
        code=REMEMBER_FAILED, message=f"memory was not written: {why}", recoverable=True
    )
