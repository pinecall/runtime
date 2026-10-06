"""The bodies of the eval doors: a golden, a case, a suite, a run, a replay, a persona, a judge."""

from datetime import date
from typing import Literal

from pydantic import Field

from pinecall.domain.agent import RunsOn
from pinecall.domain.names import Env, Json, JsonObject
from pinecall.wire.frames import WireModel
from pinecall.wire.parts import EndReason, ModelConfig, ScoreVerdict
from pinecall.wire.rest.calls import SessionScore

# How a Spanish-speaking agent addresses the caller.
type Register = Literal["tu", "usted"]


# `failed` is the run itself; a golden that failed is a score, not a status.
type RunStatus = Literal["running", "done", "failed"]


# ── a golden ──


class EventStep(WireModel):
    """A fact of the tenant's backend injected into a golden, after the caller turn it names."""

    # 0 is before the caller's first line.
    after_turn: int = 0
    name: str
    data: JsonObject = Field(default_factory=dict[str, Json])


class Expect(WireModel):
    """What a golden expects; each field set is one judge."""

    tools: list[str] = Field(default_factory=list[str])
    # Catches an action taken before consent, which a list of phrases cannot.
    not_tools: list[str] = Field(default_factory=list[str])
    not_said: list[str] = Field(default_factory=list[str], alias="not")
    says: list[str] = Field(default_factory=list[str])
    # Any one of these is enough: an expectation with several right answers.
    says_any: list[str] = Field(default_factory=list[str])
    # Every price, hour, date and name the agent stated is in the call's evidence.
    grounded: bool = False
    # A field named `register` would shadow a pydantic attribute.
    addressed_as: Register | None = Field(default=None, alias="register")
    # With `events` only: whether the agent takes the fact up.
    replies: bool | None = None


class Golden(WireModel):
    """One scripted conversation: the state it opens in, the caller's lines, what is expected."""

    name: str
    state: JsonObject = Field(default_factory=dict[str, Json])
    input: list[str] = Field(default_factory=list[str])
    # Answered to `recall` for this call only; the memory table is never written.
    memory: list[str] = Field(default_factory=list[str])
    events: list[EventStep] = Field(default_factory=list[EventStep])
    # The day the model is told it is; the real one when absent.
    today: date | None = None
    expect: Expect = Field(default_factory=Expect)
    # The call `pinecall runs promote` made it from; no judge reads it.
    promoted_from: str | None = None


# ── a suite, its run, and the matrix it is scored into ──


class RunSuiteRequest(WireModel):
    """POST /v1/evals/run, the body: the agent, its goldens and cases, the models, a version."""

    agent: str
    # Each golden is a call whose simulated caller the box pays for: two hundred at most.
    goldens: list[Golden] = Field(default_factory=list[Golden], max_length=200)
    # Cases of the org's dataset by name; a case held out is played only when named here.
    cases: list[str] = Field(default_factory=list[str])
    # Every case of the agent's that is not held out: the nightly run.
    dataset: bool = False
    # A version of the agent's settings in the app's scope to run instead of the one standing.
    version: int | None = Field(default=None, ge=1)
    # Empty: the model the agent declares.
    models: list[ModelConfig] = Field(default_factory=list[ModelConfig])
    # The app socket to drive, as `WS /v1/chat?app=`; absent, the newest that holds the agent.
    app: str | None = None
    # Each golden said out loud, through the world's fleet.
    voice: bool = False
    interferer_db: float | None = None
    packet_loss: float = Field(default=0.0, ge=0, le=1)


class OpenedCall(WireModel):
    """The call one golden opened under one model."""

    golden: str
    model: str
    call: str


class JudgeScore(WireModel):
    """One judge's verdict on one golden under one model."""

    metric: str
    # livekit's scoring: a pass is 1, unsure is 0.5, a fail is 0.
    score: float
    passed: bool
    reason: str
    criteria: str
    judge_calls: int


# `asked` is set on a cell that did not hold, and null when the run kept no requests.
class ScoreRow(WireModel):
    """Every judge's verdict on one golden under one model, and the call's own summary."""

    model: str
    golden: str
    scores: list[JudgeScore]
    summary: JsonObject | None
    asked: list[JsonObject] | None = None


class Failure(WireModel):
    """One judge that did not hold, and the cell it broke on."""

    model: str
    golden: str
    metric: str


class ScoreMatrix(WireModel):
    """Models by goldens: the axes in the order the cells came, one row per cell."""

    models: list[str]
    goldens: list[str]
    metrics: list[str]
    # A count, not a price: a judge's tokens are billed with the rest of the model usage.
    judge_calls: int
    runs: list[ScoreRow]
    failures: list[Failure]


class EvalRunResponse(WireModel):
    """An eval run: the calls it opened, the matrix judged so far, and why it stopped early."""

    id: str
    agent: str
    started_at: float
    finished_at: float | None
    status: RunStatus
    calls: list[OpenedCall]
    matrix: ScoreMatrix | None
    error: str | None


class EvalRunList(WireModel):
    """GET /v1/evals/runs: the runs of the key's org and world, newest first."""

    runs: list[EvalRunResponse]


# ── the org's dataset: real calls kept as goldens ──


class PromoteCaseRequest(WireModel):
    """POST /v1/evals/cases, the body: the finished call, its name, what it expects, held out."""

    call: str
    name: str = Field(min_length=1)
    expect: Expect = Field(default_factory=Expect)
    # Left out of a nightly run, read only when a run names it.
    held_out: bool = False


class EvalCase(WireModel):
    """One case of the org's dataset: the golden a real call made, whose, from where, by whom."""

    id: str
    agent: str
    name: str
    golden: Golden
    source_call: str
    source_env: Env
    held_out: bool
    author: str
    created_at: float


class EvalCaseList(WireModel):
    """GET /v1/evals/cases: the org's cases, by agent and name."""

    cases: list[EvalCase]


# ── the judge judged ──


class LabelRequest(WireModel):
    """POST /v1/evals/calibration, the body: what one judge should have answered on a call."""

    call: str
    judge: str = Field(min_length=1)
    held: bool
    note: str | None = None


class JudgeAgreement(WireModel):
    """One judge against the labels people gave: how many, compared, agreed, and trusted."""

    judge: str
    labelled: int
    compared: int
    agreed: int
    rate: float | None
    # None until enough labels were compared to say; False is a judge under its line.
    trusted: bool | None


class Calibration(WireModel):
    """GET /v1/evals/calibration: each judge's agreement, and the line it is held to."""

    judges: list[JudgeAgreement]
    labels_to_judge: int
    agrees_at_least: float


# ── a finished call checked again ──


class ReplayCallRequest(WireModel):
    """POST /v1/evals/replay/{call}, the body: the words banned and the latency budget."""

    banned: list[str] = Field(default_factory=list[str])
    budget: dict[str, float] = Field(default_factory=dict[str, float])


class CheckVerdict(WireModel):
    """One code check's answer: its name, its status and why."""

    check: str
    status: ScoreVerdict
    detail: str


class ReplayCallResponse(WireModel):
    """POST /v1/evals/replay/{call}, the answer: the six checks and whether none broke."""

    call: str
    agent: str
    passed: bool
    verdicts: list[CheckVerdict]


# ── the simulated caller ──


class CallerPersona(WireModel):
    """A persona as the caller doors take it: who to play, and on what."""

    name: str = ""
    # Bounded: each is a model's prompt, on keys the box may lend.
    goal: str = Field(max_length=4000)
    style: str = Field(max_length=4000)
    facts: JsonObject = Field(default_factory=dict[str, Json])
    llm: str | None = None
    tts: str | None = None
    voice: str | None = None
    # For the persona judge alone: the model playing the caller is never told it.
    accepts_when: str = ""
    declines_when: str = ""


class Spoken(WireModel):
    """One turn of the call as the caller heard it."""

    who: Literal["agent", "caller"]
    said: str = Field(max_length=4000)


class NextLineRequest(WireModel):
    """POST /v1/evals/caller, the body: the persona, the call so far, the turns left."""

    persona: CallerPersona
    # Two turns for each of the forty a call may take, and no more.
    heard: list[Spoken] = Field(default_factory=list[Spoken], max_length=80)
    turns_left: int = Field(default=1, ge=0, le=40)


class NextLineResponse(WireModel):
    """The caller's next line, and whether it hangs up after it."""

    say: str
    hangup: bool = False


class PlaceVoiceCallRequest(WireModel):
    """POST /v1/evals/voice, the body: the room, the agent, the persona, and the line."""

    # Minted by the client, as the box mints one (`call_` and a word), so it tails the log before
    # the call starts; the door takes it only for a call nobody opened yet.
    call: str = Field(pattern=r"^call_[A-Za-z0-9]{1,64}$")
    agent: str
    persona: CallerPersona
    # The caller's lines, each paid for by the box (its model and its voice): forty at most.
    turns: int = Field(default=6, ge=1, le=40)
    # dB of a background voice under the caller's; absent is a clean line.
    interferer_db: float | None = None
    packet_loss: float = Field(default=0.0, ge=0, le=1)


class PlaceVoiceCallResponse(WireModel):
    """POST /v1/evals/voice, the answer: how many lines the caller said, and on what line."""

    call: str
    turns: int
    line: str
    # What the caller's lines and voice cost, in US dollars, by the box's rates.
    caller_cost_usd: float = 0.0
    # The caller hung up because its call had spent the providers row's `caller.ceiling_usd`.
    stopped_at_ceiling: bool = False


# ── an agent's personas ──


class PersonaRequest(WireModel):
    """PUT /v1/agents/{slug}/personas/{name}, the body: the caller written whole."""

    about: str = ""
    goal: str
    style: str
    facts: dict[str, str] = Field(default_factory=dict[str, str])
    state: JsonObject = Field(default_factory=dict[str, Json])
    llm: str | None = None
    tts: str | None = None
    voice: str | None = None
    accepts_when: str | None = None
    declines_when: str | None = None
    # The name this caller had, when the write renames it.
    was: str | None = None


class PersonaRow(WireModel):
    """One caller of the agent as the list shows it."""

    name: str
    about: str
    goal: str
    style: str
    facts: dict[str, str]
    state: JsonObject
    llm: str | None
    tts: str | None
    voice: str | None
    accepts_when: str
    declines_when: str
    author: str
    set_at: float


class PersonaList(WireModel):
    """GET /v1/agents/{slug}/personas: the agent's callers, by name."""

    personas: list[PersonaRow]


class PersonaRunRow(WireModel):
    """One call a persona made: when, how long, how it ended, what it cost, the judges' score."""

    call: str
    agent: str
    started_at: float
    ended_at: float | None
    turns: int
    end_reason: EndReason | None
    outcome: str | None
    cost_usd: float | None
    score: SessionScore | None


class PersonaRunList(WireModel):
    """GET /v1/agents/{slug}/personas/{name}/runs: a page of the persona's calls, newest first."""

    runs: list[PersonaRunRow]
    total: int
    next: str | None


# ── an agent's own judges ──


class JudgeRequest(WireModel):
    """PUT /v1/agents/{slug}/judges/{name}, the body: the question, and which calls it reads."""

    question: str
    runs_on: RunsOn = "every-call"


class JudgeRow(WireModel):
    """One of the agent's own judges as the list shows it."""

    name: str
    question: str
    runs_on: RunsOn
    author: str
    set_at: float


class JudgeList(WireModel):
    """GET /v1/agents/{slug}/judges: the agent's own judges, by name."""

    judges: list[JudgeRow]
