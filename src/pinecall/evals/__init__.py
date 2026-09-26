"""The rings: what a turn does, what a tool is asked, who judges the answer, and the score."""

from pinecall.evals.case import Arrived, Called, Case, Said
from pinecall.evals.case_builder import a_case
from pinecall.evals.goldens import Register
from pinecall.evals.hangup_score import a_score
from pinecall.evals.headless_session import Headless, a_headless_call
from pinecall.evals.headless_tool_answers import Answers
from pinecall.evals.html_report import as_html
from pinecall.evals.judges.binary_question import asked
from pinecall.evals.judges.code_judge import PolicyJudge, broken, held
from pinecall.evals.judges.consent import ConsentJudge
from pinecall.evals.judges.expected import (
    EveryPhraseWasSaidJudge,
    EveryToolRanJudge,
    NoForbiddenToolRanJudge,
    NothingWasSaidJudge,
    TheCallerWasHeardJudge,
    TheEventWasAnsweredJudge,
)
from pinecall.evals.judges.grader import Counted, a_judge
from pinecall.evals.judges.grounded import (
    EXTRACTORS,
    Evidence,
    Extractor,
    GroundedJudge,
    Scope,
    evidence_of,
)
from pinecall.evals.judges.register import RegisterJudge
from pinecall.evals.matrix import GoldenRun, Matrix, Run, Score, a_matrix
from pinecall.evals.transcript import said_by_the_agent, tools_called
from pinecall.evals.voice_run import Line, a_simulated_call

__all__ = [
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
    "a_case",
    "a_headless_call",
    "a_judge",
    "a_matrix",
    "a_score",
    "a_simulated_call",
    "as_html",
    "asked",
    "broken",
    "evidence_of",
    "held",
    "said_by_the_agent",
    "tools_called",
]
