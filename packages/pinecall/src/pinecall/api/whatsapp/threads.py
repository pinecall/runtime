"""This process's open WhatsApp conversations: one per contact per number, each one a text call."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

from pinecall.api.deps import held
from pinecall.api.whatsapp.thread_deps import Doors
from pinecall.api.whatsapp.unanswered import Waiting, WaitingRoom
from pinecall.live import open_text_call, taken_up
from pinecall.live.sockets import Registration
from pinecall.orgs.admission import QuotaExhausted
from pinecall.providers.models import NoProvider
from pinecall.providers.registry import Asked, vendor_key
from pinecall.session.text.session import TextSession
from pinecall.session.text.turn_allowance import SPENT, TurnRefused
from pinecall.types import CallContext, Contact, Route, new_call_id
from pinecall.whatsapp.inbound_message import Inbound
from pinecall.whatsapp.number_routes import WHATSAPP, route_for_number
from pinecall.whatsapp.outbound_replies import watch_replies
from pinecall_protocol.defs import EndReason

logger = logging.getLogger(__name__)

# Two hours without a word closes the thread: the log is sealed, the judges run, and the next
# message opens a NEW call. It is also what honours Meta's rule below, without a second clock.
IDLE_SECONDS = 2 * 60 * 60

# Meta only lets a business send free-form text within 24 h of the customer's last message
# (developers.facebook.com/docs/whatsapp/cloud-api/guides/send-messages#customer-service-windows).
# A thread that idles out after two hours can never be older than that when it answers, so this
# number is a fact this file honours and never a timer it runs. See docs/decisions/whatsapp.md.
WINDOW_SECONDS = 24 * 60 * 60

# Only text is read here. A voice note, an image or a location is acknowledged and dropped: it
# must not open a session that would then answer a message nobody could read.
ONLY_TEXT = "whatsapp: a %s from %s was dropped — only text is read here"

# The agent is held, and the call still cannot open: no key for the model it declared, no WhatsApp
# token in either the org or the box, or the org is past a quota. Refused at the door, before the
# model has been paid for a sentence that could never have left.
NOT_ANSWERED = "whatsapp: %s cannot answer at %s: %s"

# Nothing was said for a whole idle period. The one reason a thread ends by itself.
WENT_QUIET: EndReason = "timeout"

# One turn of a thread raised. The thread goes on; the process's log says which call.
TURN_FAILED = "whatsapp: a turn on call %s failed and was not answered"


# What a thread does to end itself, given to it so it holds no reference to the table that keeps it.
type Closing = Callable[["Thread", EndReason], Awaitable[None]]


class Thread:
    """One conversation: the call it is, the queue of what is still to be heard, and its clock."""

    def __init__(
        self,
        session: TextSession,
        phone_number_id: str,
        closing: Closing,
        idle_seconds: float,
    ) -> None:
        self.session = session
        # Learned from the inbound and remembered here: the Graph API sends FROM the
        # phone_number_id, not from the number, so nothing about a WhatsApp Business Account is
        # stored in any table of this runtime. The sender of this thread is wired from it.
        self.phone_number_id = phone_number_id
        self._closing = closing
        self._said: asyncio.Queue[str] = asyncio.Queue()
        self._spoke = asyncio.Event()
        # One contact, one conversation, in order: Meta may deliver two messages a second apart
        # and a model takes seconds, so they wait in a queue rather than racing each other.
        self._pump = asyncio.ensure_future(self._draining())
        self._clock = asyncio.ensure_future(self._closing_when_idle(idle_seconds))

    def heard(self, text: str) -> None:
        """One more message on this thread. Returns at once: Meta re-delivers a slow webhook."""
        self._spoke.set()
        self._said.put_nowait(text)

    async def idle(self) -> None:
        """Everything said has been answered and nothing is in flight. For tests, and only tests."""
        await self._said.join()

    # A turn that raises — a vendor down, a tool that blew up — must not kill the pump: the thread
    # would sit there with a queue nobody drains until the idle clock buried it two hours later.
    # The failure is one line and the next message is answered.
    async def _draining(self) -> None:
        """The queue, one message at a time, in the order the contact wrote them."""
        while True:
            text = await self._said.get()
            try:
                await self.session.hears(text)
            except TurnRefused as refused:
                # The org is past a quota and the session has ended the call: the thread goes with
                # it, answering nothing, as a refused open answers nothing. The next message is
                # asked at a new open, and refused there while the quota stays spent.
                logger.warning(NOT_ANSWERED, self.session.agent, self.phone_number_id, refused)
                self._clock.cancel()
                await self._closing(self, SPENT)
                return
            except Exception:
                logger.exception(TURN_FAILED, self.session.call)
            finally:
                self._said.task_done()

    # Not a sleep per message: one waiter that is woken by every inbound and only fires when a
    # whole idle period has gone by with none. The period starts when the thread is QUIET —
    # everything said has been answered — and not at the last inbound: counted from the inbound, a
    # turn slower than the period was cancelled in the middle of its answer.
    async def _closing_when_idle(self, seconds: float) -> None:
        """Close the thread the first time it goes a whole idle period without a word."""
        while True:
            await self._said.join()
            self._spoke.clear()
            # heard() sets the flag and queues the text in one step of the loop, so a message that
            # landed between the join and the clear is still in the queue: answer it first.
            if not self._said.empty():
                continue
            try:
                await asyncio.wait_for(self._spoke.wait(), timeout=seconds)
            except TimeoutError:
                self._pump.cancel()
                await self._closing(self, WENT_QUIET)
                return


class Threads:
    """Every WhatsApp conversation this process is running, by the number and the person on it."""

    def __init__(self, idle_seconds: float = IDLE_SECONDS) -> None:
        self._open: dict[tuple[str, str], Thread] = {}
        self._idle_seconds = idle_seconds
        # What reached a number while nobody held its agent: kept, and answered when somebody does.
        self.waiting = WaitingRoom(WINDOW_SECONDS)

    def of(self, number: str, wa_id: str) -> Thread | None:
        """The thread this pair is talking on, or None when nobody has written yet."""
        return self._open.get((number, wa_id))

    # Called by the webhook and returning at once: Meta re-delivers a body its receiver was slow
    # to answer, and answering the model's turn before the 200 would double every message.
    async def received(self, doors: Doors, inbound: Inbound) -> None:
        """One message onto its thread, opening the call when this is the first of them."""
        if inbound.kind != "text" or not inbound.text:
            logger.warning(ONLY_TEXT, inbound.kind or "message", inbound.number)
            return
        thread = self.of(inbound.number, inbound.wa_id) or await self._opened(doors, inbound)
        if thread is not None:
            # What they wrote while nobody could answer goes first, in order, whoever reaches it.
            for kept in self.waiting.of(inbound.number, inbound.wa_id):
                thread.heard(kept.inbound.text or "")
                await self.waiting.taken(doors.logs, kept, thread.session.call)
            thread.heard(inbound.text)

    # The chat door's steps, in the chat door's order, with the agent found by the DOOR rather
    # than named in a URL. Every refusal is one warning line and no session: Meta is told 200
    # either way, because a webhook that answers 4xx is a webhook Meta disables.
    async def _opened(self, doors: Doors, inbound: Inbound) -> Thread | None:
        """One new call for this contact, or None and a line saying why there is none."""
        route = await route_for_number(doors.routes, inbound.number)
        if route is None:
            return None
        held = doors.registry.taking(route.env, route.agent)
        if held is None:
            # A deploy, a gateway just restarted: nobody holds the agent for a few seconds, and a
            # person who wrote in those seconds is kept waiting, never dropped.
            await self.waiting.kept(doors.logs, route, inbound)
            return None
        going = await self._taken_up(doors, inbound, route, held)
        if going is not None:
            return going
        try:
            opened = await open_text_call(
                held,
                _a_context(route, inbound),
                doors.tuning,
                doors.vault,
                doors.llms,
                doors.admission,
                doors.logs,
                doors.live.running(held.org),
                doors.lookups,
                doors.settings.budgets,
            )
            # The org's own Meta token or the box's, out of the very keys the model was built
            # from: the vault is read once per call and not once per thing the call needs.
            token = vendor_key(WHATSAPP, Asked(settings=doors.settings, keys=opened.keys))
        except (NoProvider, QuotaExhausted) as refused:
            logger.warning(NOT_ANSWERED, route.agent, inbound.number, refused)
            return None
        session = opened.session
        await doors.logs.owned(
            session.call, route.agent, held.org, held.env, held.holder, opened.versions
        )
        doors.live.serve(
            session.call,
            session.agent,
            held.org,
            doors.logs.writing(session.call, session.agent),
            held.owner,
            context=session.context,
            config=session.config,
            holder=held.holder,
        )
        doors.live.open(session)
        thread = Thread(
            session, inbound.phone_number_id, self._forgetting(doors), self._idle_seconds
        )
        # Wired from the thread, and before the first entry: whatever writes a turn.agent from
        # here on reaches the contact, and nothing has to remember to send it too.
        session.watch(
            watch_replies(session, doors.graph, token, thread.phone_number_id, inbound.wa_id)
        )
        await session.start()
        self._open[(inbound.number, inbound.wa_id)] = thread
        return thread

    # This process has no thread for the contact, but the gateway may have had one before it
    # restarted: their newest call with the agent, still open, is taken up from its log with the
    # idle clock it had left. One that went quiet for a whole idle period while nobody was
    # watching ends now, as it would have, and the message opens a new call.
    async def _taken_up(
        self, doors: Doors, inbound: Inbound, route: Route, held: Registration
    ) -> Thread | None:
        """The contact's conversation this gateway forgot, going again; None when there is none."""
        newest = await doors.index.calls_with(
            held.org, route.env, held.holder or "", route.agent, inbound.caller, 1
        )
        if not newest:
            return None
        try:
            opened = await taken_up(
                newest[0],
                held,
                _a_context(route, inbound),
                doors.index,
                doors.logs,
                doors.live,
                doors.tuning,
                doors.vault,
                doors.llms,
                doors.lookups,
                doors.settings.budgets,
                doors.admission,
            )
            if opened is None:
                return None
            token = vendor_key(WHATSAPP, Asked(settings=doors.settings, keys=opened.keys))
        except NoProvider as refused:
            logger.warning(NOT_ANSWERED, route.agent, inbound.number, refused)
            return None
        session = opened.session
        left = self._idle_seconds - (time.time() - session.quiet_since)
        thread = Thread(session, inbound.phone_number_id, self._forgetting(doors), max(left, 0.0))
        if left <= 0:
            await self._forgetting(doors)(thread, WENT_QUIET)
            return None
        session.watch(
            watch_replies(session, doors.graph, token, thread.phone_number_id, inbound.wa_id)
        )
        self._open[(inbound.number, inbound.wa_id)] = thread
        return thread

    async def answering(self, doors: Doors, waiting: Waiting) -> str | None:
        """A kept message onto its thread, now somebody holds the agent: the call it went on."""
        inbound = waiting.inbound
        thread = self.of(inbound.number, inbound.wa_id) or await self._opened(doors, inbound)
        if thread is None or waiting not in self.waiting.of(inbound.number, inbound.wa_id):
            return None
        thread.heard(inbound.text or "")
        return thread.session.call

    def _forgetting(self, doors: Doors) -> Closing:
        """How a thread of this table ends itself: the log sealed, and the row dropped."""

        async def close(thread: Thread, reason: EndReason) -> None:
            await thread.session.hangup(reason, "platform")
            doors.live.close(thread.session.call)
            doors.logs.forget(thread.session.call)
            self._drop(thread)

        return close

    def _drop(self, thread: Thread) -> None:
        """Forget the row this thread is on, whichever pair it was keyed by."""
        for door, open_thread in list(self._open.items()):
            if open_thread is thread:
                del self._open[door]


def _a_context(route: Route, inbound: Inbound) -> CallContext:
    """One call, minted here: who wrote, at which of the org's numbers, and under which agent."""
    return CallContext(
        call=new_call_id(),
        channel=WHATSAPP,
        direction="inbound",
        caller=inbound.caller,
        route=route,
        today=date.today(),
        # A person on WhatsApp is never a stranger: the number is an identity memory can hold, and
        # the name is whatever WhatsApp shows for them.
        contact=Contact(phone=inbound.caller, name=inbound.name),
    )


def get_threads(connection: HTTPConnection) -> Threads:
    """The conversations this process is running. The lifespan opened it; a test overrides it."""
    return held(connection, "threads", Threads)


ThreadsDep = Annotated[Threads, Depends(get_threads)]
