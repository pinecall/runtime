"""The provider-error check: what broke mid-call, as the session itself wrote it down."""

from __future__ import annotations

from pinecall.evals.checks.check_verdict import Verdict, broken, held
from pinecall.evals.checks.replay import Failure, Replayed

CHECK = "errors"


def errors(call: Replayed) -> Verdict:
    """An error the session did not recover from fails the call; one it recovered from is named."""
    fatal = [failure for failure in call.failures if not failure.recoverable]
    if fatal:
        return broken(CHECK, "; ".join(_said(failure) for failure in fatal))
    if call.failures:
        recovered = "; ".join(_said(failure) for failure in call.failures)
        return held(CHECK, f"recovered from {len(call.failures)} error(s): {recovered}")
    return held(CHECK, "the call logged no error")


def _said(failure: Failure) -> str:
    """One error as a person reads it: where in the log, what it was called, and what it said."""
    return f"seq {failure.seq} {failure.code}: {failure.message}"
