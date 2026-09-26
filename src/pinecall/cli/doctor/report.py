"""One line of the doctor's report, and how a failure reads on it."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    """One line of the report: what was asked, whether it answered, and why."""

    name: str
    ok: bool
    detail: str
    # A ✗ that is advice, not an outage: the box still carries a call without it, so it is
    # reported and never made the verdict.
    advisory: bool = False


def reason(failure: Exception) -> str:
    """`ConnectionRefusedError: [Errno 61] Connection refused` reads at a glance in a terminal."""
    message = str(failure).strip()
    return f"{type(failure).__name__}: {message}" if message else type(failure).__name__
