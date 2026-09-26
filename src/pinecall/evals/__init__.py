"""The rings: what a turn does, what a tool is asked, who judges the answer, and the score."""

from pinecall.evals.answers import Answers
from pinecall.evals.bridge import a_case
from pinecall.evals.calling import Line, a_simulated_call
from pinecall.evals.case import Arrived, Called, Case, Said
from pinecall.evals.goldens import Register
from pinecall.evals.headless import Headless, a_headless_call
from pinecall.evals.judges.asking import asked
from pinecall.evals.judges.consent import ConsentJudge
from pinecall.evals.judges.expected import (
    EveryPhraseWasSaidJudge,
    EveryToolRanJudge,
    NoForbiddenToolRanJudge,
    NothingWasSaidJudge,
    TheCallerWasHeardJudge,
    TheEventWasAnsweredJudge,
)
from pinecall.evals.judges.grounded import (
    EXTRACTORS,
    Evidence,
    Extractor,
    GroundedJudge,
    Scope,
    evidence_of,
)
from pinecall.evals.judges.model import Counted, a_judge
from pinecall.evals.judges.policy import PolicyJudge, broken, held
from pinecall.evals.judges.register import RegisterJudge
from pinecall.evals.matrix import GoldenRun, Matrix, Run, Score, a_matrix
from pinecall.evals.report import as_html
from pinecall.evals.score import a_score
from pinecall.evals.transcript import answered_by_the_app, said_by_the_agent, tools_called

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
    "answered_by_the_app",
    "as_html",
    "asked",
    "broken",
    "evidence_of",
    "held",
    "said_by_the_agent",
    "tools_called",
]
