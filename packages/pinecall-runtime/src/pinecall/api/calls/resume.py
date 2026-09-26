"""A text call its gateway forgot — a restart — taken up again from its log, not started anew."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from pinecall.api.calls.attachment import attach_socket
from pinecall.api.calls.opening import TextCall, open_text_session
from pinecall.live.calls import Live
from pinecall.live.sockets import Registration
from pinecall.log.entry import Entry
from pinecall.log.store.call_index import CallIndex
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.orgs.admission import Admission
from pinecall.orgs.tuning_store import TuningStore
from pinecall.orgs.vault import Vault
from pinecall.providers.models import Models
from pinecall.session.text.session import Watcher
from pinecall.settings import Budgets
from pinecall.types import CallContext
from pinecall_protocol.registry import TERMINAL_EVENT


# A text call runs in the gateway's own process, so a restart ends the session — never the call:
# its log is whole, its head row unsealed. The next time the call is spoken to — a chat socket
# that reconnects naming it, a WhatsApp contact who writes again — it is built again from the log:
# the history, the state, the counters, and the app's socket told with call.attached, so it sends
# its prompt and tools again. No quota is asked (it was admitted once), nothing is said, nothing
# is written but call.attached. None when the call is not one to take up: another org's, another
# agent's, or over.
async def taken_up(
    call: str,
    held: Registration,
    context: CallContext,
    index: CallIndex,
    logs: Logs,
    live: Live,
    tuning: TuningStore,
    vault: Vault | None,
    llms: Models,
    lookups: Lookups,
    budgets: Budgets,
    admission: Admission,
    watching: Watcher | None = None,
) -> TextCall | None:
    """The call as its log left it, served again from this process; None if it is not one."""
    corner = await index.corner_of_call(call)
    if corner is None or corner.sealed or corner.org != held.org or corner.agent != held.slug:
        return None
    entries = await logs.writing(call, held.slug).whole()
    if not entries or any(entry.type == TERMINAL_EVENT for entry in entries):
        return None
    context = _as_it_opened(replace(context, call=call), entries)
    opened = await open_text_session(
        held, context, tuning, vault, llms, logs, lookups, budgets, admission, running=None
    )
    session = opened.session
    if watching is not None:
        session.watch(watching)
    live.serve(
        call,
        session.agent,
        held.org,
        logs.writing(call, session.agent),
        None,
        context=session.context,
        config=session.config,
        holder=held.holder,
    )
    live.open(session)
    await session.resume(entries)
    await attach_socket(live, call, held.owner)
    return opened


def _as_it_opened(context: CallContext, entries: list[Entry]) -> CallContext:
    """The context with the caller and the day the call's own call.started said."""
    started = next((entry.data for entry in entries if entry.type == "call.started"), None)
    if started is None:
        return context
    caller = started.get("from") if context.direction == "inbound" else started.get("to")
    at = started.get("started_at")
    today = context.today
    if isinstance(at, int | float):
        today = date.fromtimestamp(at)
    return replace(context, caller=str(caller or context.caller), today=today)
