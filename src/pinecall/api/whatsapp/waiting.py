"""WhatsApp messages nobody could answer yet: kept on the agent's log, answered later."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pinecall.log.store import Store
from pinecall.log.writers import Logs
from pinecall.types import Env, Route
from pinecall.whatsapp.inbound import Inbound
from pinecall_protocol import encode
from pinecall_protocol.events import MessageTaken, MessageWaiting

logger = logging.getLogger(__name__)

WAITING = "message.waiting"
TAKEN = "message.taken"

# How often the room looks for an agent held again. A process between two deploys is back in
# seconds, and a person who wrote is reading their phone: a couple of seconds is not felt.
EVERY_S = 2.0

KEPT = "whatsapp: no app is holding agent %s, so %s's message waits for one"
EXPIRED = "whatsapp: a message to agent %s outlived the customer-service window unanswered"


@dataclass(frozen=True)
class Waiting:
    """One kept message: whose agent, in which world, and the message as it arrived."""

    agent: str
    env: Env
    inbound: Inbound
    received_at: float


# The log is the queue. A message that reached a number while no app held the agent — the process
# between two deploys, a gateway just restarted — is written onto the agent's own log as
# message.waiting, so a second restart loses nothing, and message.taken closes it once a socket
# holds the agent again and the message went on a call. This process keeps the open ones in memory
# as well, loaded from the log when it starts, so looking for an agent held again costs no read.
class WaitingRoom:
    """The messages waiting for somebody to answer them, oldest first."""

    def __init__(self, window_s: float) -> None:
        self._window_s = window_s
        self._waiting: dict[str, Waiting] = {}

    @property
    def waiting(self) -> tuple[Waiting, ...]:
        """What is waiting right now, oldest first."""
        return tuple(self._waiting.values())

    def of(self, number: str, wa_id: str) -> tuple[Waiting, ...]:
        """What this person wrote to this number that is still waiting, oldest first."""
        return tuple(
            waiting
            for waiting in self._waiting.values()
            if (waiting.inbound.number, waiting.inbound.wa_id) == (number, wa_id)
        )

    async def kept(self, logs: Logs, route: Route, inbound: Inbound) -> None:
        """This message waits, on the agent's log, for a socket to hold the agent."""
        now = time.time()
        said = MessageWaiting.model_validate(
            {
                "channel": "whatsapp",
                "env": route.env,
                "number": inbound.number,
                "phone_number_id": inbound.phone_number_id,
                "from": inbound.wa_id,
                "name": inbound.name,
                "message_id": inbound.message_id,
                "text": inbound.text or "",
                "received_at": now,
            }
        )
        await logs.writing_agent(route.agent).append(WAITING, encode(said))
        self._waiting[inbound.message_id] = Waiting(route.agent, route.env, inbound, now)
        logger.warning(KEPT, route.agent, inbound.number)

    async def loaded(self, store: Store) -> None:
        """Every message still waiting on any agent's log, as a process that starts finds it."""
        after = 0
        while page := await store.across([WAITING, TAKEN], after=after):
            for row in page:
                data = row.entry.data
                if row.entry.type == WAITING:
                    inbound = Inbound(
                        number=str(data["number"]),
                        phone_number_id=str(data["phone_number_id"]),
                        wa_id=str(data["from"]),
                        name=None if data.get("name") is None else str(data["name"]),
                        message_id=str(data["message_id"]),
                        kind="text",
                        text=str(data["text"]),
                    )
                    at = float(data["received_at"])
                    self._waiting[inbound.message_id] = Waiting(
                        row.entry.agent, data["env"], inbound, at
                    )
                else:
                    self._waiting.pop(str(data["message_id"]), None)
            after = page[-1].position

    # Oldest first, and one at a time: two messages of one person go on one thread, in order.
    async def answered(
        self,
        logs: Logs,
        held: Callable[[Env, str], bool],
        answer: Callable[[Waiting], Awaitable[str | None]],
    ) -> None:
        """Every waiting message whose agent somebody holds now, answered; expired ones let go."""
        for waiting in self.waiting:
            if time.time() - waiting.received_at > self._window_s:
                await self.taken(logs, waiting, None)
                logger.warning(EXPIRED, waiting.agent)
            elif held(waiting.env, waiting.agent):
                call = await answer(waiting)
                if call is not None:
                    await self.taken(logs, waiting, call)

    async def taken(self, logs: Logs, waiting: Waiting, call: str | None) -> None:
        """Off the queue, and the log says where it went."""
        said = MessageTaken(message_id=waiting.inbound.message_id, call=call)
        await logs.writing_agent(waiting.agent).append(TAKEN, encode(said))
        self._waiting.pop(waiting.inbound.message_id, None)


async def answering(
    room: WaitingRoom,
    logs: Logs,
    held: Callable[[Env, str], bool],
    answer: Callable[[Waiting], Awaitable[str | None]],
) -> None:
    """The room's loop, for the life of the gateway. A round that fails is one line, not the end."""
    while True:
        try:
            await room.answered(logs, held, answer)
        except Exception:
            logger.exception("whatsapp: the waiting room could not answer this round")
        await asyncio.sleep(EVERY_S)
