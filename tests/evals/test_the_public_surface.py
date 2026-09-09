"""What a stranger gets from `pinecall.evals`, pinned: adding a name means editing this."""

from __future__ import annotations

from pinecall import evals

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
    "Foreign",
    "GroundedJudge",
    "Headless",
    "LeakageJudge",
    "Line",
    "Matrix",
    "NoForbiddenToolRanJudge",
    "NoVoice",
    "NothingWasSaidJudge",
    "PolicyJudge",
    "Register",
    "RegisterJudge",
    "Run",
    "Said",
    "Scope",
    "Score",
    "Spoken",
    "TheEventWasAnsweredJudge",
    "a_case",
    "a_headless_call",
    "a_judge",
    "a_matrix",
    "a_score",
    "a_simulated_call",
    "answered_by_the_app",
    "as_html",
    "asked",
    "broken",
    "evidence_of",
    "held",
    "said_by_the_agent",
    "tools_called",
]


def test_the_package_exports_exactly_these_names() -> None:
    assert evals.__all__ == THE_SURFACE


def test_every_exported_name_is_actually_there() -> None:
    """An `__all__` that names something the package does not hold is a broken star import."""
    missing = [name for name in THE_SURFACE if not hasattr(evals, name)]
    assert not missing, f"exported but absent: {missing}"
