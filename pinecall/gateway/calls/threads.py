"""The WhatsApp threads this gateway keeps open, one per contact; their doors are api/threads.py."""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx
from pydantic import TypeAdapter

from pinecall.channels import routes, whatsapp
from pinecall.channels.whatsapp import IDLE_S, WINDOW_S, Inbound, Waiting
from pinecall.domain.call import CallContext, Contact, Route, new_call_id, today_in
from pinecall.domain.errors import NotAvailable, PinecallError, QuotaExhausted, UpstreamFailed
from pinecall.domain.scope import Scope
from pinecall.gateway._call_setup import exhausted
from pinecall.gateway._served import Serving
from pinecall.gateway._sockets import Registration, Sockets
from pinecall.gateway._text_calls import open_text, resume_text, tokens_of
from pinecall.gateway.calls.owners import THREAD_CHANNEL
from pinecall.log import inbox
from pinecall.postgres.pool import Pool, box_task
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


AGAIN = "whatsapp: message %s delivered again, dropped: %s"


TURN_FAILED = "whatsapp: a turn of call %s failed and was not answered"


NOT_SENT = "whatsapp_not_sent"


HANDED = "whatsapp: a message for %s was handed to the gateway holding its thread"

NOT_HANDED = "whatsapp: a message handed on by another gateway was not read: %s"

# How long the listener for messages handed on waits for the signal before listening again.
HANDED_RETRY_S = 1.0

_INBOUND: TypeAdapter[Inbound] = TypeAdapter(Inbound)

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
        self.handed: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Take the messages other gateways hand on for the threads held here."""
        self.handed = box_task(self._handed_here())

    # Meta delivers a message again when it thinks it unanswered. The row is claimed before the
    # message is read, so of two deliveries at once only one reads it; it is marked read once the
    # message is queued or kept, and a reading that fails gives the claim back, so Meta's next
    # delivery reads it again. A process that dies mid-reading leaves a claim its lease expires.
    async def received(self, inbound: Inbound) -> bool:
        """Read the message once, onto its contact's conversation; False while another reads it."""
        route = await _route_of(self.serving.connections.pool, inbound)
        if route is None:
            return True
        pool = self.serving.connections.pool
        now = self.serving.logs.store.clock()
        claim = await whatsapp.claimed(pool, route.org, inbound.message_id, now)
        if claim != "new":
            logger.info(AGAIN, inbound.message_id, claim)
            return claim == "seen"
        try:
            await self._heard(route, inbound)
        except Exception:
            await whatsapp.released(pool, route.org, inbound.message_id)
            raise
        await whatsapp.read(pool, route.org, inbound.message_id, now)
        return True

    # Returns once the message is queued or kept: Meta sends a slow webhook again.
    # A contact's thread is held by one gateway: a message landing on another is handed to it,
    # and taken here only when nobody holds it, or its holder no longer listens.
    async def _heard(self, route: Route, inbound: Inbound) -> None:
        scope = Scope(route.org, route.env)
        registration = self.sockets.taking(scope, route.agent, inbound.caller)
        door = _door(registration.scope if registration else scope, inbound)
        thread = self.open.get(door)
        holder = self.serving.live.owners.threads_elsewhere.get("|".join(door))
        if thread is None and holder is not None and await self._handed_to(holder, inbound):
            logger.info(HANDED, inbound.number)
            return
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
            # Claimed and read when it was kept: it is heard, not received again.
            route = await _route_of(self.serving.connections.pool, waiting.inbound)
            if route is not None:
                await self._heard(route, waiting.inbound)
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
        serving += [] if self.handed is None else [self.handed]
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
        self.serving.live.owners.holding("|".join(door), here=True)
        thread.served = asyncio.create_task(self._served(door, thread))
        return thread

    async def _handed_to(self, holder: str, inbound: Inbound) -> bool:
        channel = THREAD_CHANNEL.format(gateway=holder)
        try:
            return (
                await self.serving.live.signal.published(channel, _INBOUND.dump_json(inbound)) > 0
            )
        except NotAvailable:
            return False

    # Claimed and read by the gateway it landed on: here it is only heard.
    async def _handed_here(self) -> None:
        signal = self.serving.live.signal
        channel = THREAD_CHANNEL.format(gateway=self.serving.live.owners.id)
        while True:
            try:
                listening = await signal.subscribe(channel)
            except NotAvailable:
                await asyncio.sleep(HANDED_RETRY_S)
                continue
            try:
                async for data in listening:
                    await self._taken_on(_INBOUND.validate_json(data))
            finally:
                listening.close()
            await asyncio.sleep(HANDED_RETRY_S)

    async def _taken_on(self, inbound: Inbound) -> None:
        route = await _route_of(self.serving.connections.pool, inbound)
        if route is None:
            return
        try:
            await self._heard(route, inbound)
        except PinecallError as refused:
            logger.warning(NOT_HANDED, refused)

    # A conversation this process never saw is taken up from its log while it has idle time left.
    async def _taken_up(self, registration: Registration, inbound: Inbound) -> Session | None:
        newest = await inbox.calls_with(
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
                        Scope(scope.org, scope.env),
                        turns=session.call.turns,
                        tokens=tokens_of(session.usage),
                        at=self.serving.logs.store.clock(),
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
            self.serving.live.owners.holding("|".join(door), here=False)
            self.serving.live.close(session.call.context.call)
            self.serving.logs.forget(session.call.context.call)


def _door(scope: Scope, inbound: Inbound) -> Door:
    return (scope.org, scope.env, inbound.number, inbound.wa_id)


async def _route_of(pool: Pool, inbound: Inbound) -> Route | None:
    if inbound.kind != "text" or not inbound.text:
        logger.warning(NOT_TEXT, inbound.kind or "message", inbound.number)
        return None
    route = await routes.at(pool, "whatsapp", inbound.number)
    if route is None:
        logger.warning(NOBODY_AT, inbound.number, inbound.number)
    return route
