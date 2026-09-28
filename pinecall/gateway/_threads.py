"""The WhatsApp threads this gateway keeps open, one per contact; their doors are api/threads.py."""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from pinecall.channels import routes, whatsapp
from pinecall.channels.whatsapp import IDLE_S, WINDOW_S, Inbound, Waiting
from pinecall.domain.call import CallContext, Contact, Route, new_call_id, today_in
from pinecall.domain.errors import PinecallError, QuotaExhausted, UpstreamFailed
from pinecall.domain.scope import Scope
from pinecall.gateway._agents import exhausted
from pinecall.gateway._served import Serving
from pinecall.gateway._sockets import Registration, Sockets
from pinecall.gateway._text_calls import open_text, resume_text, tokens_of
from pinecall.log import queries
from pinecall.session import text
from pinecall.session.session import Session
from pinecall.tenancy import admission
from pinecall.wire.events import ErrorEvent
from pinecall.wire.frames import Entry

# One conversation: the number written to, and who wrote.
type Door = tuple[str, str, str, str]


logger = logging.getLogger(__name__)


NOT_TEXT = "whatsapp: a %s to %s was dropped: only text is read"


NOBODY_AT = "whatsapp: nobody answers %s: `pinecall numbers import %s --channel whatsapp` routes it"


NOT_ANSWERED = "whatsapp: agent %s cannot answer at %s: %s"


KEPT = "whatsapp: nobody holds agent %s, so a message to %s waits for somebody"


EXPIRED = "whatsapp: a message to agent %s outlived Meta's window unanswered"


TURN_FAILED = "whatsapp: a turn of call %s failed and was not answered"


NOT_SENT = "whatsapp_not_sent"


# However the call ends, the conversation is over with it: a written call a supervisor ends
# only says so, and its session is closed here.
OVER = {"call.ended", "supervisor.ended"}


@dataclass
class OpenThread:
    """One contact's open conversation: its session, the token it replies with, what is unread."""

    session: Session
    inbound: Inbound
    http: httpx.AsyncClient
    token: str
    # A None is the call over: the desk ended it, or it was hung up some other way.
    heard: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue[str | None])
    served: asyncio.Task[None] | None = None

    # The model's turns, the agent's `say` and a supervisor's reach the contact on one path.
    async def replied(self, entry: Entry) -> None:
        """Send a durable turn.agent to the contact; Meta's refusal is an error on the call."""
        if entry.type in OVER:
            self.heard.put_nowait(None)
            return
        answer = entry.data.get("text")
        if (
            entry.type != "turn.agent"
            or entry.ephemeral
            or not isinstance(answer, str)
            or not answer
        ):
            return
        try:
            await whatsapp.send_text(self.http, self.token, self.inbound, answer)
        except (UpstreamFailed, httpx.HTTPError) as refused:
            error = ErrorEvent(code=NOT_SENT, message=str(refused), recoverable=True)
            # Queued, not awaited: the queue is the one appending the entry this tap runs on.
            self.session.call.writing.write("error", error)


class Threads:
    """The open WhatsApp threads of this process, keyed by org, world, number and contact."""

    def __init__(self, serving: Serving, sockets: Sockets) -> None:
        """No conversation open and nothing waiting until `loaded` reads the logs."""
        self.serving = serving
        self.sockets = sockets
        self.idle_s: float = IDLE_S
        self.open: dict[Door, OpenThread] = {}
        self.waiting: list[Waiting] = []
        self.answering_now: set[asyncio.Task[None]] = set()

    # Returns once the message is queued: Meta sends a slow webhook again.
    async def received(self, inbound: Inbound) -> None:
        """Queue the message on its contact's conversation, opening one when there is none."""
        if inbound.kind != "text" or not inbound.text:
            logger.warning(NOT_TEXT, inbound.kind or "message", inbound.number)
            return
        route = await routes.at(self.serving.connections.pool, "whatsapp", inbound.number)
        if route is None:
            logger.warning(NOBODY_AT, inbound.number, inbound.number)
            return
        scope = Scope(route.org, route.env)
        registration = self.sockets.taking(scope, route.agent, inbound.caller)
        thread = self.open.get(_door(registration.scope if registration else scope, inbound))
        if thread is None and registration is None:
            self.waiting.append(await whatsapp.kept(self.serving.logs, route, inbound))
            logger.warning(KEPT, route.agent, inbound.number)
            return
        if thread is None and registration is not None:
            thread = await self._opened(registration, route, inbound)
        if thread is not None:
            thread.heard.put_nowait(inbound.text)

    # Off the app socket's own loop: opening a conversation may wait on the model.
    def answering(self, registration: Registration) -> None:
        """Answer the messages that waited for this agent, now that somebody holds it."""
        if not any(waiter.agent == registration.slug for waiter in self.waiting):
            return
        task = asyncio.create_task(self._answered(registration))
        self.answering_now.add(task)
        task.add_done_callback(self.answering_now.discard)

    # Oldest first, so one contact's messages reach their conversation in order.
    async def _answered(self, registration: Registration) -> None:
        now = self.serving.logs.store.clock()
        for waiting in [waiter for waiter in self.waiting if waiter.agent == registration.slug]:
            if waiting.env != registration.scope.env:
                continue
            self.waiting.remove(waiting)
            if now - waiting.received_at > WINDOW_S:
                logger.warning(EXPIRED, waiting.agent)
                await whatsapp.taken(self.serving.logs, waiting, None)
                continue
            await self.received(waiting.inbound)
            opened = self.open.get(_door(registration.scope, waiting.inbound))
            await whatsapp.taken(
                self.serving.logs,
                waiting,
                None if opened is None else opened.session.call.context.call,
            )

    async def loaded(self) -> None:
        """What was waiting when this process started, read back off the agents' logs."""
        self.waiting = await whatsapp.waiting_in(self.serving.logs.store)

    async def closed(self) -> None:
        """Stop answering: the threads stay open in their logs for the next process."""
        serving = [value.served for value in self.open.values() if value.served is not None]
        serving += list(self.answering_now)
        for task in serving:
            task.cancel()
        await asyncio.gather(*serving, return_exceptions=True)
        self.open.clear()

    async def _opened(
        self, registration: Registration, route: Route, inbound: Inbound
    ) -> OpenThread | None:
        token = await whatsapp.meta_token_for(
            self.serving.connections.pool,
            self.serving.connections.vault,
            route.org,
            inbound.phone_number_id,
        )
        if token is None:
            logger.warning(NOT_ANSWERED, route.agent, inbound.number, "no Meta token here")
            return None
        try:
            taken = await self._taken_up(registration, inbound)
            session = taken or await open_text(
                self.serving, registration, self._context(registration, route, inbound)
            )
        except PinecallError as refused:
            logger.warning(NOT_ANSWERED, route.agent, inbound.number, refused)
            return None
        thread = OpenThread(
            session=session, inbound=inbound, http=self.serving.connections.http, token=token
        )
        # Before the session starts, so every turn the contact is owed reaches them.
        served = self.serving.live.calls[session.call.context.call]
        served.log.tapped(thread.replied)
        if taken is None:
            await session.start()
        door = _door(registration.scope, inbound)
        self.open[door] = thread
        thread.served = asyncio.create_task(self._served(door, thread))
        return thread

    # A conversation this process never saw is taken up from its log while it has idle time left.
    async def _taken_up(self, registration: Registration, inbound: Inbound) -> Session | None:
        newest = await queries.calls_with(
            self.serving.connections.pool,
            registration.scope,
            registration.slug,
            inbound.caller,
            limit=1,
        )
        if not newest:
            return None
        entries = await self.serving.logs.store.whole(newest[0])
        if not entries or self.serving.logs.store.clock() - entries[-1].ts > self.idle_s:
            return None
        return await resume_text(
            self.serving, registration, newest[0], self.serving.connections.settings.timezone
        )

    def _context(self, registration: Registration, route: Route, inbound: Inbound) -> CallContext:
        answering = Route(
            org=route.org,
            agent=registration.slug,
            channel="whatsapp",
            number=route.number,
            env=route.env,
        )
        return CallContext(
            call=new_call_id(),
            channel="whatsapp",
            direction="inbound",
            caller=inbound.caller,
            route=answering,
            today=today_in(self.serving.connections.settings.timezone),
            contact=Contact(phone=inbound.caller, name=inbound.name),
            holder=registration.scope.holder or None,
        )

    # The idle period starts once every message is answered: a slow turn is never cut off.
    async def _served(self, door: Door, thread: OpenThread) -> None:
        session = thread.session
        scope = session.call.context.route
        try:
            while True:
                try:
                    answer = await asyncio.wait_for(thread.heard.get(), timeout=self.idle_s)
                except TimeoutError:
                    await text.end(session, "timeout", "platform")
                    return
                if answer is None:
                    await session.close()
                    return
                try:
                    await admission.admit_turn(
                        self.serving.connections.pool,
                        scope.org,
                        scope.env,
                        turns=session.call.turns,
                        tokens=tokens_of(session.usage),
                    )
                except QuotaExhausted as refused:
                    await exhausted(self.serving.logs, scope.org, session.call.config.slug, refused)
                    await text.end(session, "timeout", "platform")
                    return
                try:
                    await text.hears(session, answer)
                except PinecallError:
                    logger.warning(TURN_FAILED, session.call.context.call, exc_info=True)
        finally:
            self.open.pop(door, None)
            self.serving.live.close(session.call.context.call)
            self.serving.logs.forget(session.call.context.call)


def _door(scope: Scope, inbound: Inbound) -> Door:
    return (scope.org, scope.env, inbound.number, inbound.wa_id)
