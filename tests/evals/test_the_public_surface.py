"""What a stranger gets from `pinecall.evals`, pinned: adding a name means editing this."""

from __future__ import annotations

import pytest

from pinecall import evals

pytestmark = pytest.mark.unit

THE_SURFACE = [
    "EXTRACTORS",
    "Answers",
    "Arrived",
    "Called",
    "Case",
    "ConsentJudge",
    "Counted",
    "EveryPhraseWasSaidJudge",
    "EveryToolRanJudge",
    "Evidence",
    "Extractor",
    "GoldenRun",
    "GroundedJudge",
    "Headless",
    "Line",
    "Matrix",
    "NoForbiddenToolRanJudge",
    "NothingWasSaidJudge",
    "PolicyJudge",
    "Register",
    "RegisterJudge",
    "Run",
    "Said",
    "Scope",
    "Score",
    "TheCallerWasHeardJudge",
    "TheEventWasAnsweredJudge",
    "ask_judge",
    "broken",
    "build_case",
    "build_judge",
    "build_matrix",
    "evidence_of",
    "held",
    "open_headless_call",
    "render_html",
    "run_simulated_call",
    "said_by_the_agent",
    "score_call",
    "tools_called",
]


def test_the_package_exports_exactly_these_names() -> None:
    assert evals.__all__ == THE_SURFACE


def test_every_exported_name_is_actually_there() -> None:
    """An `__all__` that names something the package does not hold is a broken star import."""
    missing = [name for name in THE_SURFACE if not hasattr(evals, name)]
    assert not missing, f"exported but absent: {missing}"
