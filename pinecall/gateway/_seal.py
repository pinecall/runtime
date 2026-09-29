"""How a call ends: what it taught memory, what it cost, how it was judged, and its log sealed."""

import asyncio
import logging
from collections.abc import Sequence

from pinecall.domain.agent import AgentConfig, Model
from pinecall.domain.errors import PinecallError, QuotaExhausted
from pinecall.evals import judges
from pinecall.gateway._call_setup import exhausted, keys_of
from pinecall.gateway._served import Served, Serving, now_of
from pinecall.log import facts
from pinecall.log.logs import Log
from pinecall.log.reduce import phone_legs, reduce
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.process.connections import Connections
from pinecall.providers import catalog, credentials, prices
from pinecall.providers.catalog import Providers
from pinecall.providers.credentials import Keyring, thinking
from pinecall.retrieval import extraction, lookups, memory
from pinecall.retrieval.extraction import MemoryWrite
from pinecall.tenancy import admission, orgs, vault
from pinecall.tenancy.judges import StoredJudge, for_call
from pinecall.wire.events import CallEnded, CallScore, CallSummary, ErrorEvent, MemoryOps
from pinecall.wire.frames import Entry
from pinecall.wire.parts import MemoryOp
from pinecall.wire.rest.calls import SealCallRequest
from pinecall.wire.state import AgentTurn

logger = logging.getLogger(__name__)

JUDGING_OFF = "this org's calls are not judged at hang-up: POST /v1/evals/judge/{call} judges one"

A_RUN_JUDGES_IT = "an eval run opened this call, and its own judges scored it in the run's matrix"

NO_JUDGE = "this box's providers configuration names no judge model"

NO_CEILING = "the providers row gives the judge a ceiling of zero, so no judge model may be asked"

JUDGING_BROKE = "judging this call failed: {broke}"


REMEMBER_FAILED = "the call was not written into memory: {why}"


# What a hang-up waits for the one model call that writes memory, unless the settings say.
REMEMBER_BUDGET_S = 8.0


# One end for every call: a worker's, a written one's, and one the reaper finishes.
async def sealed(
    serving: Serving, served: Served, sealing: SealCallRequest, *, lent: Sequence[str] = ()
) -> None:
    """Remember, price the call, write its summary and its score, seal the log, let it go."""
    written = await remembered(serving, served)
    # The memory model's tokens are the call's: they are billed with it.
    if written is not None and written.usage is not None:
        sealing = sealing.model_copy(update={"usage": [*sealing.usage, written.usage]})
    await summed_up(serving.connections.pool, serving.logs.store, served.log, sealing)
    if lent:
        await facts.lent(serving.connections.pool, served.call, lent)
    if served.context.run is not None:
        score = CallScore(judges=[], judge_calls=0, not_judged=A_RUN_JUDGES_IT)
    elif not await orgs.judged(serving.connections.pool, served.scope.org):
        score = CallScore(judges=[], judge_calls=0, not_judged=JUDGING_OFF.format(call=served.call))
    else:
        entries = await serving.logs.store.whole(served.call)
        own = await for_call(serving.connections.pool, served.scope.org, served.agent)
        score = await judged_call(serving.connections, entries, served.config, own)
    await served.log.append("call.score", score.written())
    serving.logs.forget(served.call)
    serving.live.close(served.call)


# Between call.ended and call.summary, on the org's keys as they are now. A refusal at the cap goes
# on the agent's log and the call's says an empty remember; a break is an entry, and it seals.
async def remembered(serving: Serving, served: Served) -> MemoryWrite | None:
    """What the call taught its contact's memory, written; None when the agent keeps nothing."""
    pool = serving.connections.pool
    entries = await serving.logs.store.whole(served.call)
    heard = lookups.heard_in(served.context, served.config, entries, at=now_of(serving))
    if heard is None or serving.embedder is None:
        return None
    try:
        await admission.admit_memory(
            pool,
            served.scope.org,
            served.scope.env,
            kept=await memory.kept(pool, served.scope.org, served.scope.env),
        )
    except QuotaExhausted as refused:
        await exhausted(serving.logs, served.scope.org, served.agent, refused)
        op = MemoryOp(op="remember", contact=heard.contact, facts=[], took_ms=0.0)
        await served.log.append("memory.ops", MemoryOps(ops=[op]).written())
        return MemoryWrite(op=op, usage=None)
    budget = serving.connections.settings.remember_budget_s or REMEMBER_BUDGET_S
    try:
        async with asyncio.timeout(budget):
            configured = await catalog.providers(pool)
            keys = await keys_of(pool, serving.connections.vault, served.scope)
            model = thinking(served.config, configured, keys)
            written = await extraction.remember(pool, serving.embedder, model, served.scope, heard)
    except Exception as broke:
        # Memory is a courtesy to the next call; this one ends all the same.
        logger.warning("call %s was not written into memory", served.call, exc_info=True)
        why = REMEMBER_FAILED.format(why=str(broke) or type(broke).__name__)
        failed = ErrorEvent(code="remember_failed", message=why, recoverable=True)
        await served.log.append("error", failed.written())
        return None
    await served.log.append("memory.ops", MemoryOps(ops=[written.op]).written())
    return written


# The seal never fails on a judge: a call that could not be judged says why and seals all the same.
async def judged_call(
    connections: Connections,
    entries: Sequence[Entry],
    declared: AgentConfig | None,
    own: Sequence[StoredJudge],
) -> CallScore:
    """The hang-up panel and the agent's own judges over a finished call, a model's when named."""
    call = next((entry.call for entry in entries if entry.call is not None), "")
    try:
        configured = await catalog.providers(connections.pool)
        judge = await judge_of(connections, configured)
        questions = [stored.judge for stored in own]
        return await judges.at_hangup(entries, declared, questions, judge, configured=configured)
    except PinecallError as broke:
        logger.warning("call %s: nothing judged it", call, exc_info=True)
        return CallScore(judges=[], judge_calls=0, not_judged=JUDGING_BROKE.format(broke=broke))


# Always the box's key, never an org's: judging is the platform's measure, the same for all.
async def judge_of(connections: Connections, configured: Providers) -> judges.JudgeModel:
    """The judge model on the box's key, or None and the sentence that says why there is none."""
    if configured.judge is None:
        return judges.JudgeModel(None, 0.0, NO_JUDGE)
    if configured.judge.ceiling_usd <= 0:
        return judges.JudgeModel(None, 0.0, NO_CEILING)
    box = await vault.box_credentials(connections.pool, connections.vault)
    named = configured.judge.llm
    declared = Model(provider=named.vendor, model=named.model or "")
    stage = credentials.stage("llm", declared, configured, Keyring(box=box))
    return judges.JudgeModel(stage, configured.judge.ceiling_usd)


async def summed_up(pool: Pool, store: Store, log: Log, sealing: SealCallRequest) -> None:
    """call.summary: how the call ended, what it used, what that cost."""
    entries = await store.whole(log.name)
    ended = next((entry for entry in reversed(entries) if entry.type == "call.ended"), None)
    over = None if ended is None else CallEnded.model_validate(ended.data)
    state = reduce(entries)
    summary = CallSummary(
        reason="error" if over is None else over.reason,
        outcome=sealing.outcome,
        duration_s=0.0 if over is None else over.duration_s,
        turns=sum(1 for turn in state.turns if isinstance(turn, AgentTurn)),
        usage=sealing.usage,
        cost=prices.cost(sealing.usage, await catalog.providers(pool), legs=phone_legs(entries)),
        recording=sealing.recording,
    )
    await log.append("call.summary", summary.written())
