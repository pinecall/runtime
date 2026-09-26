"""One eval run: every golden under every model, one run per agent, written down as it goes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from functools import partial
from typing import Annotated
from uuid import uuid4

from fastapi import Depends
from pydantic import Field
from starlette.requests import HTTPConnection

from pinecall._exceptions import PinecallError
from pinecall._settings import Budgets, Settings
from pinecall.api.agents.held_agent import Registration
from pinecall.api.agents.registry import NO_AGENT, Registry
from pinecall.api.agents.session_config import tuned_for
from pinecall.api.deps import held
from pinecall.api.evals.golden_call import run_golden_conversation
from pinecall.api.evals.golden_judges import Judging
from pinecall.api.evals.run_attachment import AppDetached, Attachment
from pinecall.api.evals.spoken_golden import run_spoken_conversation
from pinecall.api.live import Live
from pinecall.evals.caller_voice import Speaking
from pinecall.evals.goldens import Golden
from pinecall.evals.run_store import EvalRun, Opened, Runs
from pinecall.evals.voice_run import Line
from pinecall.log.store import Store
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.orgs.admission import Admission
from pinecall.orgs.tuning_store import TuningStore
from pinecall.orgs.vault import Vault, brought_by
from pinecall.providers import declaration
from pinecall.providers.models import Models
from pinecall.session.text.turn_allowance import TurnRefused
from pinecall.types import (
    AgentConfig,
    Brought,
    DeclarationRefused,
    Env,
    Model,
    Versions,
    new_call_id,
)
from pinecall_protocol import WireModel, defs

# The design says SIGKILL, and there is no child to signal: a run is coroutines in the gateway's
# own loop. So the deadline is asyncio's, and what it kills is the conversation in flight — the
# calls already made keep their logs, and the run is stored as failed with this many minutes named.
A_RUN_MAY_TAKE_S = 15 * 60

A_RUN = "run_"

# The column a conversation is drawn under when nobody named a model and the agent declared none:
# whichever model the vendor table's default is, which is the vendor file's own business.
DECLARED = "declared"

ALREADY_RUNNING = (
    "eval run {id} is running on agent {agent}: a run drives the app that also answers that "
    "agent's real calls, so there is one per agent at a time"
)

TOOK_TOO_LONG = "the run passed its {minutes} minute deadline and was stopped"

# A person closed the terminal holding the class, or the process died. Every conversation left
# would be put to a bare model with no view and no tools, and its score would grade an agent that
# was not there — so the run stops here and the row says how far it got. See eval-runner.md.
THE_APP_LEFT = (
    "the app detached after {done} of {total} goldens ({slug}): the socket holding the agent "
    "closed, and a conversation with no app behind it drives a model with no view and no tools"
)


class AlreadyRunning(PinecallError):
    """A second run was asked for while one is in flight."""


class NobodyServing(PinecallError):
    """No app is holding the agent this run names, so there is nothing to evaluate."""


class Wanted(WireModel):
    """What one POST asks for: the agent, its goldens, and the models to put them through."""

    agent: str
    goldens: list[Golden]
    # Empty means the model the agent itself declared, which is the single column `pinecall test`
    # prints when nobody asked for a comparison.
    models: list[defs.ModelConfig] = Field(default_factory=list[defs.ModelConfig])
    # Which app socket to run against, as `WS /v1/chat?app=` names one. Absent takes whichever
    # socket the chat door would give a caller.
    app: str | None = None
    # Ring 2: the same goldens, said out loud on a real line instead of written into a text
    # session. The turns are the tenant's own lines either way — what changes is that a voice
    # says them, ears hear them and the worker holds the session, which is the whole point.
    voice: bool = False
    # How spoilt the caller's line is, for a spoken run. None is a clean line.
    interferer_db: float | None = None
    packet_loss: float = 0.0


@dataclass(frozen=True)
class Process:
    """What a run needs of the process it runs in: everything a text call is opened with."""

    registry: Registry
    tuning: TuningStore
    llms: Models
    logs: Logs
    live: Live
    store: Store
    runs: Runs
    # The world the key that asked opens, and whose corner of it: the run is put to the app
    # holding the agent THERE, so a laptop's suite never drives the box's agent, the box's never
    # drives a laptop's, and one developer's never drives another's.
    env: Env
    holder: str | None
    # Where the org's own provider keys are kept, or None on a runtime that keeps nobody's, and
    # the org's gate: which of the box's keys the run's models may use, and whether the org may
    # still pay for each written turn — a golden run is a text call and is held to the same quotas.
    vault: Vault | None
    admission: Admission
    # What runs a golden's lookups and remembers its hang-up, and how long a turn waits.
    lookups: Lookups
    budgets: Budgets
    # What a spoken run needs to reach the media plane: the LiveKit pair and its url. A written
    # run never touches it.
    settings: Settings


class Runner:
    """This process's eval runs. It holds one fact: which run, if any, each agent is under."""

    def __init__(self) -> None:
        self._in_flight: dict[str, str] = {}

    def in_flight(self, agent: str) -> str | None:
        """The id of the run driving this agent right now, or None while nobody is driving it."""
        return self._in_flight.get(agent)

    # Per agent and not per gateway: a run drives ONE app socket, so two agents held by two apps
    # never meet — two people on one gateway test at the same time. See eval-runner.md.
    # Refused and never queued: a caller who waits behind a fifteen-minute run has no way of
    # knowing it is waiting, and the id in the refusal is what they poll instead.
    @asynccontextmanager
    async def alone(self, id: str, agent: str) -> AsyncGenerator[None]:
        """Hold one agent for one run. A second asker for it is told which run has it."""
        running = self._in_flight.get(agent)
        if running is not None:
            raise AlreadyRunning(ALREADY_RUNNING.format(id=running, agent=agent))
        self._in_flight[agent] = id
        try:
            yield
        finally:
            del self._in_flight[agent]


async def run_evals(wanted: Wanted, runner: Runner, process: Process) -> EvalRun:
    """Every golden under every model, scored, stored, and answered as one finished run."""
    serving = process.registry.serving(process.env, wanted.agent, wanted.app, process.holder)
    if serving is None:
        raise NobodyServing(NO_AGENT.format(slug=wanted.agent))
    if wanted.voice:
        _refuse_what_a_spoken_run_cannot_do(wanted)
    # The same config a real caller would reach: what the org set is on this run too, because a
    # golden that tested something else would test nothing. Resolved in the corner that serves.
    resolved = await tuned_for(
        process.tuning,
        serving.org,
        serving.env,
        serving.holder,
        wanted.agent,
        serving.config,
    )
    config = resolved.config
    # Whose keys this run's conversations are answered on, read once as the run opens: a run is
    # one org's, and a suite is one session — the chat socket asks the same question per call.
    brought = await brought_by(process.vault, process.admission.quotas_of, serving.org)
    run = EvalRun(id=f"{A_RUN}{uuid4().hex[:12]}", agent=wanted.agent, started_at=time.time())
    async with runner.alone(run.id, wanted.agent):
        await process.runs.put(run)
        judging = Judging(config)
        try:
            async with asyncio.timeout(A_RUN_MAY_TAKE_S):
                run = await _every_conversation(
                    wanted, run, config, serving, process, judging, brought, resolved.versions
                )
                return await _finished(run, process)
        # Not raised: the run failed, and the row that says so — with every golden scored before
        # it, and none of the ones never opened — is exactly what the caller asked for.
        except AppDetached as left:
            return await _stopped(run, str(left), process)
        # The org may not pay for one more written turn: the run stops where its quota did, the
        # goldens scored before it kept, and the sentence names the quota (credits.exhausted is
        # already on the agent's log). A refusal is not the runner breaking, so it is not raised.
        except TurnRefused as refused:
            return await _stopped(run, str(refused), process)
        except TimeoutError:
            gave_up = TOOK_TOO_LONG.format(minutes=A_RUN_MAY_TAKE_S // 60)
            return await _stopped(run, gave_up, process)
        except Exception as broke:
            await _stopped(run, str(broke), process)
            raise
        finally:
            await judging.close()


# One conversation per golden per model, and the row rewritten twice for each: once before its
# first turn, naming the call, so a person watching sees which golden is being driven and can tail
# its log through the very doors a live call is tailed through; once when it has been judged, so
# the verdict is on the row the moment it settles and not when the last golden is done.
async def _every_conversation(
    wanted: Wanted,
    run: EvalRun,
    config: AgentConfig,
    serving: Registration,
    process: Process,
    judging: Judging,
    brought: Brought,
    versions: Versions,
) -> EvalRun:
    """The run as it stands after every call has been made and judged."""
    app = Attachment(process.registry, serving.agent, serving.owner)
    models = _the_models(wanted)
    total = len(models) * len(wanted.goldens)
    judged = 0
    for asked in models:
        running = (
            config if asked is None else declaration.apply_declaration(config, _only_the_llm(asked))
        )
        named = _named(running.llm)
        llm = process.llms(running.llm, brought)
        for golden in wanted.goldens:
            # Asked before the call is minted, so a golden the app will never answer is not opened
            # at all: the matrix ends where the app did, and the rest is absent rather than red.
            if not app.held:
                raise _the_app_left(judged, total, wanted.agent)
            call = new_call_id()
            run = run.opening(Opened(golden=golden.name, model=named, call=call))
            await process.runs.put(run)
            try:
                said = (
                    await run_spoken_conversation(
                        golden,
                        call=call,
                        run=run.id,
                        model=named,
                        agent=wanted.agent,
                        store=process.store,
                        settings=process.settings,
                        org=serving.org,
                        env=serving.env,
                        holder=serving.holder,
                        line=Line(
                            interferer_db=wanted.interferer_db, packet_loss=wanted.packet_loss
                        ),
                        app=serving.owner,
                        speaking=Speaking(
                            language=config.language,
                            agents_voice=None if config.voice is None else config.voice.voice_id,
                            brought=brought,
                        ),
                    )
                    if wanted.voice
                    else await run_golden_conversation(
                        golden,
                        call=call,
                        run=run.id,
                        model=named,
                        config=running,
                        org=serving.org,
                        env=serving.env,
                        app=app,
                        logs=process.logs,
                        live=process.live,
                        llm=llm,
                        store=process.store,
                        lookups=process.lookups,
                        budgets=process.budgets,
                        versions=versions,
                        allowance=partial(process.admission.a_turn, serving.org, wanted.agent),
                    )
                )
            # The call itself has already ended as app_detached; what this adds is the run's own
            # arithmetic, which only the loop knows.
            except AppDetached as left:
                raise _the_app_left(judged, total, wanted.agent) from left
            await judging.judged(said)
            judged += 1
            run = replace(run, matrix=judging.matrix)
            await process.runs.put(run)
    return run


# A spoken run reaches the model through the WORKER, which builds its session from the agent's
# own declaration and the operator's knobs — this process never gets to swap the llm for a column.
# Rather than print a matrix whose two columns ran the same model, the door says so.
A_SPOKEN_RUN_HAS_ONE_MODEL = (
    "--voice runs the model the agent declares: the worker builds the session, so a run cannot "
    "put a golden through a second model out loud. Drop --model, or drop --voice."
)

# The worker seeds the clock from the day the call is opened on, and a dispatch carries no date.
# A golden that pins a weekday would run on today and read as a red that is nobody's fault.
A_SPOKEN_RUN_CANNOT_PIN_A_DAY = (
    "golden {name} pins today={day}, and a spoken call runs on the real day: the worker seeds its "
    "clock when the room opens. Run this golden without --voice."
)


def _refuse_what_a_spoken_run_cannot_do(wanted: Wanted) -> None:
    """The two things ring 2 cannot honour, said before a single room is opened."""
    if wanted.models:
        raise DeclarationRefused(A_SPOKEN_RUN_HAS_ONE_MODEL)
    for golden in wanted.goldens:
        if golden.today is not None:
            raise DeclarationRefused(
                A_SPOKEN_RUN_CANNOT_PIN_A_DAY.format(name=golden.name, day=golden.today)
            )


def _the_app_left(judged: int, total: int, slug: str) -> AppDetached:
    """Why the run stopped, as a person reads it: how far it got, of how many, and whose app."""
    return AppDetached(THE_APP_LEFT.format(done=judged, total=total, slug=slug))


async def _finished(run: EvalRun, process: Process) -> EvalRun:
    """The run closed: the matrix every conversation wrote into it is now the whole of it."""
    finished = replace(run, status="done", finished_at=time.time())
    await process.runs.put(finished)
    return finished


async def _stopped(run: EvalRun, why: str, process: Process) -> EvalRun:
    """The run broke: it keeps the calls it did make, and the row says what stopped it."""
    # The stored row is ahead of the value in hand — every conversation wrote itself into it as it
    # opened — and a run that broke halfway is exactly the one whose calls a person needs.
    latest = await process.runs.of(run.id) or run
    failed = replace(latest, status="failed", finished_at=time.time(), error=why)
    await process.runs.put(failed)
    return failed


def _the_models(wanted: Wanted) -> tuple[defs.ModelConfig | None, ...]:
    """The models to run under. None is the one the agent declared, and it is the only default."""
    return tuple(wanted.models) if wanted.models else (None,)


def _only_the_llm(asked: defs.ModelConfig) -> defs.AgentConfig:
    """A configure that carries one field, so the gateway's own conversion swaps the model."""
    return defs.AgentConfig(llm=asked)


def _named(model: Model | None) -> str:
    """The column this conversation is drawn under: the model that answered, as a person says it."""
    return f"{model.provider}/{model.model}" if model is not None else DECLARED


# ── how a route asks for it ─────────────────────────────────────────────────────


def get_runner(connection: HTTPConnection) -> Runner:
    """The run this process is doing right now, if it is doing one."""
    return held(connection, "evals", Runner)


RunnerDep = Annotated[Runner, Depends(get_runner)]
