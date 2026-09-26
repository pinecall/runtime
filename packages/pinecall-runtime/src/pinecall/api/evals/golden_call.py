"""One golden, driven to hang-up against the app that is holding the agent: the same text call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from pinecall.api.agents import call_commands as commands
from pinecall.api.evals.app_settled import Settling
from pinecall.api.evals.run_attachment import APP_DETACHED, ENDED_BY, AppDetached, Attachment
from pinecall.auth.scopes import new_visitor_identity
from pinecall.evals.golden_memory import Remembering
from pinecall.evals.goldens import Golden
from pinecall.live.calls import Live
from pinecall.log.entry import Entry
from pinecall.log.replay import whole
from pinecall.log.store import Store
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.providers.models import Chat
from pinecall.session.model_requests import Asking, NotAsking, WhatWasAsked
from pinecall.session.text.session import TextSession
from pinecall.session.text.turn_allowance import Allowance, unlimited_allowance
from pinecall.settings import Budgets
from pinecall.types import AgentConfig, CallContext, Env, Route, Versions
from pinecall_protocol.commands import CallEvent, SessionConfigure


@dataclass(frozen=True)
class Conversation:
    """One golden run to hang-up: the model that answered it, the call it opened, and its log."""

    golden: Golden
    model: str
    call: str
    entries: Sequence[Entry]
    # Every request this call made, as the provider received it — or None when nobody kept them:
    # a spoken run's requests are built in the worker process and never reach this one.
    asked: Sequence[Mapping[str, Any]] | None = None


async def run_golden_conversation(
    golden: Golden,
    *,
    call: str,
    run: str,
    model: str,
    config: AgentConfig,
    org: str,
    env: Env,
    app: Attachment,
    logs: Logs,
    live: Live,
    llm: Chat,
    store: Store,
    lookups: Lookups,
    budgets: Budgets,
    versions: Versions | None = None,
    allowance: Allowance = unlimited_allowance,
) -> Conversation:
    """Open the call, seed its state, say every turn, hang up, and read the log back whole."""
    # A run is the one reader allowed the prompt itself: a golden that breaks has to be openable
    # turn by turn, and a hash in the log cannot be read. api/evals/golden_judges.py keeps the
    # broken.
    asked = WhatWasAsked()
    session = open_eval_call(
        golden,
        call,
        run,
        config,
        org,
        env,
        logs,
        llm,
        lookups,
        budgets,
        asking=asked,
        allowance=allowance,
    )
    settling = Settling(session)
    await logs.owned(session.call, session.agent, org, env, app.holder, versions)
    live.serve(
        session.call,
        session.agent,
        org,
        logs.writing(session.call, session.agent),
        app.socket,
        context=session.context,
        config=session.config,
        holder=app.holder,
    )
    live.open(session)
    try:
        await session.start()
        await app.driving(_the_whole_golden(session, golden, settling))
        await session.hangup("caller_hung_up", "caller")
    # The app that renders the view and runs the tools is gone: whatever is left of this golden
    # would be put to a bare model, so the call ends here and says why, in one entry.
    except AppDetached:
        await session.hangup(APP_DETACHED, ENDED_BY)
        raise
    finally:
        live.close(session.call)
        # The log is sealed and its readers have finished; nothing more will ever be appended.
        logs.forget(session.call)
    return Conversation(
        golden=golden,
        model=model,
        call=session.call,
        entries=await whole(store, session.call),
        asked=asked.turns,
    )


def open_eval_call(
    golden: Golden,
    call: str,
    run: str,
    config: AgentConfig,
    org: str,
    env: Env,
    logs: Logs,
    llm: Chat,
    lookups: Lookups,
    budgets: Budgets,
    asking: Asking = NotAsking(),  # noqa: B008 — stateless, shared on purpose
    allowance: Allowance = unlimited_allowance,
) -> TextSession:
    """One call under the id the run named, opened by that run, on the config this model runs."""
    # The caller is nobody — no browser minted a visitor id and no number dialled — so it gets the
    # identity the chat door mints for a caller that arrived with none. What tells this call apart
    # from a person's is not its name but the run that opened it, on the call's first entry.
    context = CallContext(
        call=call,
        channel="web",
        direction="inbound",
        caller=new_visitor_identity(),
        run=run,
        route=Route(org=org, agent=config.slug, channel="web", number=None, env=env),
        # A golden that names a weekday pins the day it means; the rest run on the real one.
        today=golden.today or date.today(),
    )
    # logs.writing() keeps the log, so every SSE reader of this call is already subscribed to it:
    # a run is tailed while it happens through the very doors a live call is tailed through.
    # A golden's lookups run the way a live text call's do: the same object, the same
    # budgets, so `docs.sources` is on the run's log for the grounded judge to read.
    return TextSession(
        context,
        config,
        logs.writing(call, config.slug),
        llm,
        # A golden that seeds `memory` answers its own facts to `recall` and nothing else moves:
        # the tool call, the tool result and the request are the real ones. evals/golden_memory.py.
        lookup=Remembering(lookups, golden.memory) if golden.memory else lookups,
        # Never the golden's: a run must not write facts about a caller nobody called as.
        rememberer=lookups,
        budgets=budgets,
        asking=asking,
        # A golden's turns are the org's turns: past `messages` or `llm_tokens` the run fails
        # with the sentence, and credits.exhausted is on the agent's log (orgs/admission.py).
        allowance=allowance,
    )


async def _the_whole_golden(session: TextSession, golden: Golden, settling: Settling) -> None:
    """The seeded state and every turn of it: everything the app is needed for, in one coroutine."""
    # The app renders its opening prompt from call.started; the first turn waits for it, or the
    # model would be asked to answer with no view at all.
    await settling.settled()
    await commands.configure(session, SessionConfigure(state=dict(golden.state)))
    await settling.settled()
    await _every_turn(session, golden, settling)


async def _every_turn(session: TextSession, golden: Golden, settling: Settling) -> None:
    """The caller's turns in order, with the golden's facts injected where it declared them."""
    await _the_facts_of(session, golden, settling, after_turn=0)
    for number, said in enumerate(golden.input, 1):
        await session.hears(said)
        # A tool may have moved the app's state; the re-render lands a moment after the answer.
        await settling.settled()
        await _the_facts_of(session, golden, settling, after_turn=number)


# The runner stands in for the tenant's backend here: a fact enters through the very door the app
# socket's `call.event` enters through, so an event the agent never declared is refused for a
# golden exactly as it is refused for a live call.
async def _the_facts_of(
    session: TextSession, golden: Golden, settling: Settling, *, after_turn: int
) -> None:
    """Every fact this golden injects at this point of the conversation, in the order written."""
    for fact in golden.events_after(after_turn):
        await commands.record_event(session, CallEvent(name=fact.name, data=dict(fact.data)))
        # The app's own handler runs on event.received: it may move state, and it may make the
        # agent speak. Whatever it does is the reply the golden is about, so it is waited for.
        await settling.settled()
