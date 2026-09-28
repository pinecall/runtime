"""WhatsApp's written calls: one per contact, answered in order, closed when quiet, kept waiting."""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from pinecall.channels import routes, telephony, whatsapp
from pinecall.channels.whatsapp import IDLE_S, WINDOW_S, Inbound, Waiting
from pinecall.domain.errors import PinecallError, QuotaExhausted, UpstreamFailed
from pinecall.domain.types import CallContext, Contact, Corner, Route, new_call_id, today_in
from pinecall.gateway.live import (
    Gated,
    Registration,
    Registry,
    exhausted,
    open_text,
    resume_text,
    tokens_of,
)
from pinecall.log import index
from pinecall.session import text
from pinecall.session.session import Session
from pinecall.tenancy import admission
from pinecall.wire.events import ErrorEvent
from pinecall.wire.frames import Entry

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

# One conversation: the number written to, and who wrote.
type Door = tuple[str, str, str, str]


@dataclass
class Thread:
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
        said = entry.data.get("text")
        if entry.type != "turn.agent" or entry.ephemeral or not isinstance(said, str) or not said:
            return
        try:
            await whatsapp.send_text(self.http, self.token, self.inbound, said)
        except (UpstreamFailed, httpx.HTTPError) as refused:
            error = ErrorEvent(code=NOT_SENT, message=str(refused), recoverable=True)
            # Queued, not awaited: the queue is the one appending the entry this tap runs on.
            self.session.call.writing.write("error", error)


class Threads:
    """The open WhatsApp conversations of this process, keyed by org, world, number and contact."""

    def __init__(
        self, gated: Gated, registry: Registry, http: httpx.AsyncClient, zone: str
    ) -> None:
        """No conversation open and nothing waiting until `loaded` reads the logs."""
        self.gated = gated
        self.registry = registry
        self.http = http
        self.zone = zone
        self.idle_s: float = IDLE_S
        self.open: dict[Door, Thread] = {}
        self.waiting: list[Waiting] = []
        self.answering_now: set[asyncio.Task[None]] = set()

    # Returns once the message is queued: Meta sends a slow webhook again.
    async def received(self, inbound: Inbound) -> None:
        """Queue the message on its contact's conversation, opening one when there is none."""
        if inbound.kind != "text" or not inbound.text:
            logger.warning(NOT_TEXT, inbound.kind or "message", inbound.number)
            return
        route = await routes.at(self.gated.pool, "whatsapp", inbound.number)
        if route is None:
            logger.warning(NOBODY_AT, inbound.number, inbound.number)
            return
        corner = Corner(route.org, route.env)
        held = self.registry.taking(corner, route.agent, inbound.caller)
        thread = self.open.get(_door(held.corner if held else corner, inbound))
        if thread is None and held is None:
            self.waiting.append(await whatsapp.kept(self.gated.logs, route, inbound))
            logger.warning(KEPT, route.agent, inbound.number)
            return
        if thread is None and held is not None:
            thread = await self._opened(held, route, inbound)
        if thread is not None:
            thread.heard.put_nowait(inbound.text)

    # Off the app socket's own loop: opening a conversation may wait on the model.
    def answering(self, held: Registration) -> None:
        """Answer the messages that waited for this agent, now that somebody holds it."""
        if not any(one.agent == held.slug for one in self.waiting):
            return
        task = asyncio.create_task(self._answered(held))
        self.answering_now.add(task)
        task.add_done_callback(self.answering_now.discard)

    # Oldest first, so one contact's messages reach their conversation in order.
    async def _answered(self, held: Registration) -> None:
        now = self.gated.logs.store.clock()
        for waiting in [one for one in self.waiting if one.agent == held.slug]:
            if waiting.env != held.corner.env:
                continue
            self.waiting.remove(waiting)
            if now - waiting.received_at > WINDOW_S:
                logger.warning(EXPIRED, waiting.agent)
                await whatsapp.taken(self.gated.logs, waiting, None)
                continue
            await self.received(waiting.inbound)
            opened = self.open.get(_door(held.corner, waiting.inbound))
            await whatsapp.taken(
                self.gated.logs,
                waiting,
                None if opened is None else opened.session.call.context.call,
            )

    async def loaded(self) -> None:
        """What was waiting when this process started, read back off the agents' logs."""
        self.waiting = await whatsapp.waiting_in(self.gated.logs.store)

    async def closed(self) -> None:
        """Stop answering: the conversations stay open in their logs for the next process."""
        serving = [one.served for one in self.open.values() if one.served is not None]
        serving += list(self.answering_now)
        for task in serving:
            task.cancel()
        await asyncio.gather(*serving, return_exceptions=True)
        self.open.clear()

    async def _opened(self, held: Registration, route: Route, inbound: Inbound) -> Thread | None:
        token = await telephony.meta_token_for(
            self.gated.pool, self.gated.vault, route.org, inbound.phone_number_id
        )
        if token is None:
            logger.warning(NOT_ANSWERED, route.agent, inbound.number, "no Meta token here")
            return None
        try:
            taken = await self._taken_up(held, inbound)
            session = taken or await open_text(
                self.gated, held, self._context(held, route, inbound)
            )
        except PinecallError as refused:
            logger.warning(NOT_ANSWERED, route.agent, inbound.number, refused)
            return None
        thread = Thread(session=session, inbound=inbound, http=self.http, token=token)
        served = self.gated.live.calls[session.call.context.call]
        # Before the session starts, so every turn the contact is owed reaches them.
        served.log.tapped(thread.replied)
        if taken is None:
            await session.start()
        door = _door(held.corner, inbound)
        self.open[door] = thread
        thread.served = asyncio.create_task(self._served(door, thread))
        return thread

    # A conversation this process never saw is taken up from its log while it has idle time left.
    async def _taken_up(self, held: Registration, inbound: Inbound) -> Session | None:
        newest = await index.calls_with(
            self.gated.pool, held.corner, held.slug, inbound.caller, limit=1
        )
        if not newest:
            return None
        entries = await self.gated.logs.store.whole(newest[0])
        if not entries or self.gated.logs.store.clock() - entries[-1].ts > self.idle_s:
            return None
        return await resume_text(self.gated, held, newest[0], self.zone)

    def _context(self, held: Registration, route: Route, inbound: Inbound) -> CallContext:
        answering = Route(
            org=route.org, agent=held.slug, channel="whatsapp", number=route.number, env=route.env
        )
        return CallContext(
            call=new_call_id(),
            channel="whatsapp",
            direction="inbound",
            caller=inbound.caller,
            route=answering,
            today=today_in(self.zone),
            contact=Contact(phone=inbound.caller, name=inbound.name),
            holder=held.corner.holder or None,
        )

    # The idle period starts once every message is answered: a slow turn is never cut off.
    async def _served(self, door: Door, thread: Thread) -> None:
        session = thread.session
        corner = session.call.context.route
        try:
            while True:
                try:
                    said = await asyncio.wait_for(thread.heard.get(), timeout=self.idle_s)
                except TimeoutError:
                    await text.end(session, "timeout", "platform")
                    return
                if said is None:
                    await session.close()
                    return
                try:
                    await admission.admit_turn(
                        self.gated.pool,
                        corner.org,
                        corner.env,
                        turns=session.call.turns,
                        tokens=tokens_of(session.usage),
                    )
                except QuotaExhausted as refused:
                    await exhausted(self.gated.logs, corner.org, session.call.config.slug, refused)
                    await text.end(session, "timeout", "platform")
                    return
                try:
                    await text.hears(session, said)
                except PinecallError:
                    logger.warning(TURN_FAILED, session.call.context.call, exc_info=True)
        finally:
            self.open.pop(door, None)
            self.gated.live.close(session.call.context.call)
            self.gated.logs.forget(session.call.context.call)


def _door(corner: Corner, inbound: Inbound) -> Door:
    return (corner.org, corner.env, inbound.number, inbound.wa_id)
