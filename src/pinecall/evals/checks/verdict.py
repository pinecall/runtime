"""What a code check answers: one line a person can read, and the word a script branches on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Four words, and every check speaks all four the same way: it held, it did not, nobody judged it
# because the runtime does not do that yet, or nothing this call carried could be judged at all.
type Status = Literal["passed", "failed", "deferred", "skipped"]


@dataclass(frozen=True)
class Verdict:
    """One check over one call: its name, how it went, and the sentence that says why."""

    check: str
    status: Status
    detail: str

    @property
    def as_json(self) -> dict[str, str]:
        """The verdict as the door answers it: three strings, no nesting, nothing to decode."""
        return {"check": self.check, "status": self.status, "detail": self.detail}


def passed(check: str, detail: str) -> Verdict:
    """The check held."""
    return Verdict(check=check, status="passed", detail=detail)


def failed(check: str, detail: str) -> Verdict:
    """The check did not hold, and the detail names what in the log says so."""
    return Verdict(check=check, status="failed", detail=detail)


def deferred(check: str, detail: str) -> Verdict:
    """The runtime does not do this yet, on purpose and with a date: not a failure of the call."""
    return Verdict(check=check, status="deferred", detail=detail)


def skipped(check: str, detail: str) -> Verdict:
    """Nothing in this call could be judged, and the detail says what was missing."""
    return Verdict(check=check, status="skipped", detail=detail)
