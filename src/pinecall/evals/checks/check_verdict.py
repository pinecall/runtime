"""What a code check answers: one line a person can read, and the word a script branches on."""

from __future__ import annotations

from dataclasses import dataclass

from pinecall_protocol.defs import ScoreVerdict


# The four words every judge of this runtime speaks (pinecall_protocol.defs.ScoreVerdict), and
# every check speaks them the same way: it held, it was broken, nobody could settle it because
# the runtime does not do that yet, or nothing this call carried could be judged at all.
@dataclass(frozen=True)
class Verdict:
    """One check over one call: its name, how it went, and the sentence that says why."""

    check: str
    status: ScoreVerdict
    detail: str


def held(check: str, detail: str) -> Verdict:
    """The check held."""
    return Verdict(check=check, status="held", detail=detail)


def broken(check: str, detail: str) -> Verdict:
    """The check did not hold, and the detail names what in the log says so."""
    return Verdict(check=check, status="broken", detail=detail)


def deferred(check: str, detail: str) -> Verdict:
    """Nobody judged it: the runtime does not do that yet, and the detail says what is missing."""
    return Verdict(check=check, status="deferred", detail=detail)


def skipped(check: str, detail: str) -> Verdict:
    """Nothing this call carried could be judged, and the detail says why."""
    return Verdict(check=check, status="skipped", detail=detail)
