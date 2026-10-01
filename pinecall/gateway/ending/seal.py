"""How a call ends: what it taught memory, what it cost, how it was judged, and its log sealed."""

import asyncio
import logging
from collections.abc import Sequence

import psycopg

from pinecall.domain.agent import AgentConfig, Model
from pinecall.domain.errors import NotAvailable, PinecallError, QuotaExhausted
from pinecall.evals import judges
from pinecall.evals.compliance import Compliance, Panel
from pinecall.gateway._call_setup import exhausted, keys_of
from pinecall.gateway._served import Served, Serving, now_of
from pinecall.log import drift, facts
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
from pinecall.tenancy import admission, consents, disclosure, orgs, policy, spend, vault
from pinecall.tenancy.judges import StoredJudge, for_call
from pinecall.wire.events import CallEnded, CallSummary, ErrorEvent, MemoryOps, SpendUnusual
from pinecall.wire.frames import Entry
from pinecall.wire.parts import MemoryOp
from pinecall.wire.rest.calls import SealCallRequest
from pinecall.wire.scores import CallScore
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

# How long one gateway holds the right to seal a call, renewed at a third of it while it seals:
# a gateway that dies sealing lets the call go within these seconds, and a knock waiting on it
# takes the seal over. Held 120 s unrenewed, a worker gave up (30 s) on a call whose sealing gateway
# died, and the reaper sealed it later with no usage: measured with a gateway killed every minute.
LEASED_S = 15.0
LOOKED_AGAIN_S = 0.5

# How long a knock waits on another gateway's seal before it says so: the worker asks again.
WAITED_AT_MOST_S = 25.0

SEALED_ELSEWHERE = (
    "call {call} is being sealed by another gateway, which has not finished: ask again"
)


# One end for every call: a worker's, a written one's, and one the reaper finishes. A call is
# sealed once: a second knock waits for the first and finds the call gone, and a seal that broke
# after its summary goes on from the score, so no call is remembered or billed twice. Across
# gateways the head's lease does what the lock does here: a knock that finds it taken waits for
# the log to say sealed.
async def sealed(
    serving: Serving, served: Served, sealing: SealCallRequest, *, lent: Sequence[str] = ()
) -> None:
    """Remember, price the call, write its summary and its score, seal the log, let it go."""
    async with served.sealing:
        if served.call not in serving.live.calls:
            return
        store = serving.logs.store
        if not await store.lease_seal(served.call, LEASED_S) and await _sealed_elsewhere(
            serving, served
        ):
            return
        renewing = asyncio.create_task(_renewed(store, served.call))
        try:
            entries = await store.whole(served.call)
            if all(entry.type != "call.summary" for entry in entries):
                await _priced(serving, served, sealing, lent)
            score = await _scored(serving, served)
            await served.log.append("call.score", score.written())
        except BaseException:
            await store.release_seal(served.call)
            raise
        finally:
            renewing.cancel()
            await asyncio.gather(renewing, return_exceptions=True)
        await drifted(serving.connections.pool, served.call, entries, score)
        await _watched(serving, served)
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
    org_facts: Compliance | None,
) -> CallScore:
    """The hang-up panel, compliance and the agent's own judges over a finished call."""
    call = next((entry.call for entry in entries if entry.call is not None), "")
    try:
        configured = await catalog.providers(connections.pool)
        judge = await judge_of(connections, configured)
        judged_by = Panel(own=[stored.judge for stored in own], compliance=org_facts)
        return await judges.at_hangup(entries, declared, judged_by, judge, configured=configured)
    except PinecallError as broke:
        logger.warning("call %s: nothing judged it", call, exc_info=True)
        return CallScore(judges=[], judge_calls=0, not_judged=JUDGING_BROKE.format(broke=broke))


# The org as the call spoke for it: its name, the opening sentence it set, and an opt-out it took.
async def compliance_of(
    pool: Pool, org: str, call: str, declared: AgentConfig | None
) -> Compliance:
    """What the compliance judges read beyond the call's log."""
    found = await orgs.find(pool, org)
    name = org if found is None else found.name
    kept = (await policy.policy_of(pool, org)).policy
    language = None if declared is None else declared.language
    opening = disclosure.disclosure_of(kept, name, language)
    return Compliance(
        org=name, disclosure=opening, opted_out=await consents.opted_out_on(pool, call)
    )


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


# Drift is a measure of the day, not of the call: a fold that breaks is logged and the call seals
# all the same, and `pinecall-runtime drift rebuild` counts it later.
async def drifted(pool: Pool, call: str, entries: Sequence[Entry], score: CallScore) -> None:
    """Count the call's stages and verdicts into its day's drift, or log why not."""
    try:
        await drift.fold(pool, call, entries, score)
    except psycopg.Error:
        logger.warning("call %s was not counted into its day's drift", call, exc_info=True)


async def summed_up(pool: Pool, store: Store, log: Log, sealing: SealCallRequest) -> None:
    """call.summary: how the call ended, what it used, what that cost."""
    entries = await store.whole(log.name)
    ended = next((entry for entry in reversed(entries) if entry.type == "call.ended"), None)
    over = None if ended is None else CallEnded.model_validate(ended.data)
    state = reduce(entries)
    duration = 0.0 if over is None else over.duration_s
    configured = await catalog.providers(pool)
    summary = CallSummary(
        reason="error" if over is None else over.reason,
        outcome=sealing.outcome,
        duration_s=duration,
        turns=sum(1 for turn in state.turns if isinstance(turn, AgentTurn)),
        usage=sealing.usage,
        cost=prices.cost(sealing.usage, configured, legs=phone_legs(entries), seconds=duration),
        recording=sealing.recording,
    )
    await log.append("call.summary", summary.written())


async def _priced(
    serving: Serving, served: Served, sealing: SealCallRequest, lent: Sequence[str]
) -> None:
    written = await remembered(serving, served)
    # The memory model's tokens are the call's: they are billed with it.
    if written is not None and written.usage is not None:
        sealing = sealing.model_copy(update={"usage": [*sealing.usage, written.usage]})
    await summed_up(serving.connections.pool, serving.logs.store, served.log, sealing)
    if lent:
        await facts.lent(serving.connections.pool, served.call, lent)


async def _scored(serving: Serving, served: Served) -> CallScore:
    pool = serving.connections.pool
    if served.context.run is not None:
        return CallScore(judges=[], judge_calls=0, not_judged=A_RUN_JUDGES_IT)
    if not await orgs.judged(pool, served.scope.org):
        return CallScore(judges=[], judge_calls=0, not_judged=JUDGING_OFF.format(call=served.call))
    entries = await serving.logs.store.whole(served.call)
    own = await for_call(pool, served.scope.org, served.agent)
    org_facts = await compliance_of(pool, served.scope.org, served.call, served.config)
    return await judged_call(serving.connections, entries, served.config, own, org_facts)


# True once another gateway sealed it; False when its lease ran out (that gateway died sealing)
# and this one took it over, so the caller seals.
async def _sealed_elsewhere(serving: Serving, served: Served) -> bool:
    store = serving.logs.store
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAITED_AT_MOST_S
    while not await store.sealed(served.call):
        if await store.lease_seal(served.call, LEASED_S):
            return False
        if loop.time() > deadline:
            raise NotAvailable(SEALED_ELSEWHERE.format(call=served.call))
        await asyncio.sleep(LOOKED_AGAIN_S)
    serving.logs.forget(served.call)
    serving.live.close(served.call)
    return True


async def _renewed(store: Store, call: str) -> None:
    while True:
        await asyncio.sleep(LEASED_S / 3)
        await store.renew_seal(call, LEASED_S)


# The org's spend is watched once the summary priced the call: today against its own trailing
# weeks, said once a day on the agent's log and held up on /metrics for the alert. A check that
# breaks is logged and the call seals all the same.
async def _watched(serving: Serving, served: Served) -> None:
    """Say once a day, on the agent's log, that the org spends more today than it usually does."""
    pool, org = serving.connections.pool, served.scope.org
    at = serving.logs.store.clock()
    try:
        found = await spend.unusual(pool, org, at)
        serving.counters.spending(org, None if found is None else found.multiple)
        if found is None or await spend.said_today(pool, org, at):
            return
        text = SpendUnusual(
            org=org,
            day=found.day,
            today_usd=found.today_usd,
            usual_usd=found.usual_usd,
            multiple=found.multiple,
        )
        await serving.logs.agent(served.agent).append("spend.unusual", text.written())
    except psycopg.Error:
        logger.warning("call %s: the org's spend was not looked at", served.call, exc_info=True)
