"""The steps every text door takes before the first word, in the order they have to happen."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial

from pinecall._settings import Budgets
from pinecall.api.agents.held_agent import Registration, SocketId
from pinecall.api.agents.registry import Registry
from pinecall.api.agents.session_config import tuned_for
from pinecall.evals.hangup_score import JudgedWhen
from pinecall.log.logs import CallLog
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.orgs.admission import Admission
from pinecall.orgs.tuning_store import TuningStore
from pinecall.orgs.vault import Vault, brought_by
from pinecall.providers.declaration import rang
from pinecall.providers.models import Models
from pinecall.session.first_entries import arrival_entry
from pinecall.session.text.session import TextSession
from pinecall.types import CallContext, Env, ProviderKeys, Versions
from pinecall_protocol import encode


# The keys travel back out because a channel may need one of its own: WhatsApp sends its answer
# with the org's Meta token, read from the very same set the model was built from, so the vault is
# asked once per call and not once per thing that call needs.
@dataclass(frozen=True)
class TextCall:
    """A text call built and not yet started: the session, and whose keys it runs on."""

    session: TextSession
    keys: ProviderKeys
    # Which tuning and lexicon the session was built on, for the head row the door claims next.
    versions: Versions


# One order, two doors: the chat socket and the WhatsApp webhook both come through here, so a call
# is refused for the same reasons in the same sequence whichever way the caller arrived. Each
# refusal is the caller's to say in their own words — NoProvider from the model, QuotaExhausted
# from admission — because a socket closes with a reason and a webhook answers Meta with a 200.
async def open_text_call(
    held: Registration,
    context: CallContext,
    tuning: TuningStore,
    vault: Vault | None,
    llms: Models,
    admission: Admission,
    logs: Logs,
    running: int,
    lookups: Lookups,
    budgets: Budgets,
) -> TextCall:
    """The config, whose keys, the model and the quota — then the session, unstarted."""
    return await open_text_session(
        held, context, tuning, vault, llms, logs, lookups, budgets, admission, running
    )


# The same session, for a call that was admitted once already and is only being taken up again —
# its gateway restarted and forgot it (api/calls/resume.py): no quota is asked a second time.
async def open_text_session(
    held: Registration,
    context: CallContext,
    tuning: TuningStore,
    vault: Vault | None,
    llms: Models,
    logs: Logs,
    lookups: Lookups,
    budgets: Budgets,
    admission: Admission,
    running: int | None,
) -> TextCall:
    """The config, whose keys, the model — and the quota, unless `running` is None — unstarted."""
    # What the org set is on this call too: a text call reads the config through the same
    # resolving function the worker's config door reads it through, in the corner that serves it.
    resolved = await tuned_for(tuning, held.org, held.env, held.holder, held.slug, held.config)
    config = resolved.config
    # Asked at the moment the call opens and never held for the next one: a tenant who rotated a
    # key a minute ago is answered on the new one, and an org that brought none runs on the box's.
    brought = await brought_by(vault, admission.quotas_of, held.org)
    # Before anything is accepted: a process with no key for the provider the agent declared
    # refuses the call at the door rather than dying in the middle of somebody's turn.
    llm = llms(config.llm, brought)
    # The org's quotas, also before: credits.exhausted lands in the agent's log either way.
    if running is not None:
        await admission.a_call(held.org, held.slug, running)
    # logs.writing() keeps the log, so every SSE reader of this call is already subscribed to it.
    # And the judge: a session judges nothing itself, so whoever opens a call hands it one.
    # This is that place for a written call, as `worker/main.py` is for a spoken one.
    # The gateway is the session here, so its lookups run in-process on the same object a worker
    # reaches over HTTP, under the same budgets. See docs/decisions/memory.md.
    session = TextSession(
        context,
        config,
        logs.writing(context.call, held.slug),
        llm,
        score=JudgedWhen(lambda _call: admission.judges(held.org)),
        lookup=lookups,
        rememberer=lookups,
        budgets=budgets,
        # And the org's quotas again before every turn the model answers, a taken-up call's too:
        # one open chat must not run on past what the org may spend (orgs/admission.py:a_turn).
        allowance=partial(admission.a_turn, held.org, held.slug),
    )
    return TextCall(session=session, keys=brought.keys, versions=resolved.versions)


# The call's first entry, unless another door already wrote it.
#
# An outbound call's was written where the call was ASKED for: the dial door knew both numbers and
# the name of whoever asked before any job existed, and its 202 handed back a call id a console
# starts reading at once. The worker opens that same log and adds nothing to the top of it — a
# second call.dialing would be the one that had forgotten who asked. Only that door ever opens a
# call with direction outbound, so this is the whole of the rule.
async def record_arrival(log: CallLog, context: CallContext, agent: str) -> None:
    """call.ringing on a call that rang. Nothing on one this gateway placed itself."""
    if context.direction == "outbound":
        return
    type, event = arrival_entry(context, context.route.number or agent)
    await log.append(type, encode(event))


# Which corner serves a call, by how the call ARRIVED.
#
# A call a key holder opened — the web widget, `pinecall chat`, a dev verb — lands in that key
# holder's corner, as it always has. A call that RANG is the org's door: the worker that dialled
# it holds a key naming nobody, so the corner is asked of the registry, which answers whose phone
# dialled and then whose line it is. `declaration.rang()` is the one place the two are told apart.
def serving_agent(
    registry: Registry,
    env: Env,
    agent: str,
    app: SocketId | None,
    context: CallContext,
    holder: str | None,
) -> Registration | None:
    """The socket this call is handed to, or None when nobody would take it."""
    if app is not None:
        return registry.on(env, agent, app)
    if rang(context.route):
        return registry.taking(env, agent, context.caller)
    return registry.serving(env, agent, None, holder)
