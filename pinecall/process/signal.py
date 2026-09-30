"""The live signal between gateways: lossy publish and subscribe, on Redis or inside one process."""

import asyncio
import logging
from collections import deque
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Self
from uuid import uuid4

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from pinecall.domain.errors import NotAvailable, SettingsRefused
from pinecall.process.settings import Settings

logger = logging.getLogger(__name__)

# Ours start here: LiveKit shares the Redis, and a channel's name is global to it, whatever db.
PREFIX = "pinecall:"

# How far a listener may fall behind before it is dropped, as a log's live reader is; how many
# messages wait for Redis before the oldest is dropped (a reader resumes from the store); and how
# many go in one pipeline, so the sender writes to Redis once a turn, not once a message.
LISTENING_DEPTH = 256
UNSENT_AT_MOST = 10_000
MOST_A_PIPELINE = 512

# How long a connect, a command or the confirmation of a subscription may take.
ANSWER_S = 2.0
# The listening connection is asked every PING_S, and is lost when silent this long; one read
# waits TICK_S before the listener looks at its clock again.
PING_S = 5.0
SILENT_AT_MOST_S = 15.0
TICK_S = 1.0

# Redis away: a lost connection is tried again at once, then from half a second doubling to five.
FIRST_PAUSE_S = 0.5
LONGEST_PAUSE_S = 5.0
# The channel each process listens on from the moment it connects: what keeps it subscribed.
OWN = "signal:{id}"

# What the log and the operator read when the signal cannot do its part.
DOWN = "the live signal between gateways is down: no Redis answers at PINECALL_REDIS_URL"
NOT_A_URL = "PINECALL_REDIS_URL is not a Redis URL ({why}): redis://host:port/db"
SILENT = "Redis said nothing for {seconds:.0f} s, not even to a ping"
LOST = "the live signal lost Redis: every listener resumes from the store; trying again"
BEHIND_OUR_BACK = "the client reconnected the listening connection, missing what came between"
NOT_SENT = "the live signal cannot publish to Redis: its messages are dropped until it can"


@dataclass(frozen=True)
class Heard:
    """One message off the listening connection: its kind, its channel, what it carried."""

    kind: str
    channel: str | None
    data: bytes | None


class Listening:
    """One listener of one channel, in order; ended once closed, or dropped when cut off."""

    def __init__(self, channel: str, leave: Callable[["Listening"], None]) -> None:
        """Nothing heard yet; `leave` takes it off the channel."""
        self.channel = channel
        # True when it fell behind or its connection was lost: what it missed is the store's.
        self.dropped = False
        self._leave = leave
        self._heard: deque[bytes] = deque()
        self._moved = asyncio.Event()
        self._over = False

    def offer(self, data: bytes) -> bool:
        """Keep the message for the listener; False once it is over."""
        if self._over:
            return False
        if len(self._heard) >= LISTENING_DEPTH:
            self.drop()
            return False
        self._heard.append(data)
        self._moved.set()
        return True

    def drop(self) -> None:
        """End it as cut off, after what it was already given."""
        self.dropped = True
        self._finish()

    def close(self) -> None:
        """End it after what it was already given, and stop listening."""
        self._finish()
        self._leave(self)

    def _finish(self) -> None:
        self._over = True
        self._moved.set()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> bytes:
        """The next message, in the order it was published; the end once it is over."""
        while not self._heard:
            if self._over:
                raise StopAsyncIteration
            self._moved.clear()
            await self._moved.wait()
        return self._heard.popleft()


class LocalSignal:
    """The signal inside one process: a gateway alone on its box, with no PINECALL_REDIS_URL."""

    def __init__(self) -> None:
        """Nobody listening."""
        self.id = f"gw_{uuid4().hex[:12]}"
        self._listening: dict[str, set[Listening]] = {}

    @property
    def up(self) -> bool:
        """Always: nothing between the publisher and the listener can go away."""
        return True

    async def connected(self) -> None:
        """Return at once: a process is always connected to itself."""

    def publish(self, channel: str, data: bytes) -> None:
        """Hand the message to every listener of the channel, never waiting for one."""
        for listening in tuple(self._listening.get(channel, ())):
            if not listening.offer(data):
                self._left(listening)

    async def subscribe(self, channel: str) -> Listening:
        """A listener of what is published on the channel from now on."""
        listening = Listening(channel, self._left)
        self._listening.setdefault(channel, set()).add(listening)
        return listening

    async def close(self) -> None:
        """End every listener."""
        for listeners in list(self._listening.values()):
            for listening in tuple(listeners):
                listening.close()

    def _left(self, listening: Listening) -> None:
        listeners = self._listening.get(listening.channel, set())
        listeners.discard(listening)
        if not listeners:
            self._listening.pop(listening.channel, None)


# Lossy on purpose: a message published while Redis is away, or to a listener that fell behind,
# is gone, and whoever listened resumes from the store. redis-py retries nothing here (its PubSub
# would subscribe again behind our back and hide what was missed): the reconnection is ours.
class RedisSignal:
    """The signal on Redis: one connection publishes in pipelines, one listens; both come back."""

    def __init__(self, url: str, *, prefix: str = PREFIX) -> None:
        """Not connected until `start`; the URL is refused here when it is not one."""
        self.id = f"gw_{uuid4().hex[:12]}"
        self.prefix = prefix
        # Whether the listening connection is subscribed now: a subscription is refused otherwise.
        self.up = False
        # Set with `up`, for whoever waits for the connection to come back.
        self._connected = asyncio.Event()
        # Messages published and never sent.
        self.dropped = 0
        # Two pools, so the listening connection is never handed to the publisher after it.
        self._client = _client_of(url, self.id)
        self._listener = _client_of(url, self.id)
        self._unsent: deque[tuple[str, bytes]] = deque()
        self._waiting = asyncio.Event()
        self._listening: dict[str, set[Listening]] = {}
        self._confirmed: dict[str, asyncio.Future[bool]] = {}
        self._leaving: set[str] = set()
        self._pubsub: PubSub | None = None
        self._reconnected = False
        self._tasks: list[asyncio.Task[None]] = []

    # What a reader cut off waits for before it follows its channels again.
    async def connected(self) -> None:
        """Return once the listening connection is subscribed, at once when it is."""
        await self._connected.wait()

    def start(self) -> None:
        """Start sending and listening; a Redis that is away is waited for, never a failure."""
        self._tasks = [asyncio.create_task(self._sending()), asyncio.create_task(self._listened())]

    def publish(self, channel: str, data: bytes) -> None:
        """Queue the message for every gateway listening on the channel; never waits for Redis."""
        if len(self._unsent) >= UNSENT_AT_MOST:
            self._unsent.popleft()
            self.dropped += 1
        self._unsent.append((self.prefix + channel, data))
        self._waiting.set()

    # Returns once Redis confirmed the subscription: whatever is published after it is heard.
    async def subscribe(self, channel: str) -> Listening:
        """A listener of what is published on the channel from now on; NotAvailable while down."""
        pubsub = self._pubsub
        if not self.up or pubsub is None:
            raise NotAvailable(DOWN)
        name = self.prefix + channel
        listening = Listening(channel, self._left)
        self._listening.setdefault(name, set()).add(listening)
        confirmed = self._confirmed.get(name)
        try:
            if confirmed is None:
                confirmed = self._confirmed[name] = asyncio.get_running_loop().create_future()
                await pubsub.subscribe(name)
            async with asyncio.timeout(ANSWER_S):
                is_confirmed = await asyncio.shield(confirmed)
        except (RedisError, OSError, TimeoutError):
            is_confirmed = False
        except asyncio.CancelledError:
            listening.close()
            raise
        if not is_confirmed:
            listening.close()
            raise NotAvailable(DOWN)
        return listening

    async def close(self) -> None:
        """Stop both connections; every listener ends, and what was not sent is dropped."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for listeners in list(self._listening.values()):
            for listening in tuple(listeners):
                listening.close()
        await self._forget_the_connection()
        await self._client.aclose()
        await self._listener.aclose()

    async def _sending(self) -> None:
        pause = 0.0
        while True:
            await self._waiting.wait()
            self._waiting.clear()
            while self._unsent:
                taken = min(len(self._unsent), MOST_A_PIPELINE)
                batch = [self._unsent.popleft() for _ in range(taken)]
                try:
                    async with self._client.pipeline(transaction=False) as pipe:
                        for name, data in batch:
                            pipe.publish(name, data)  # pyright: ignore[reportUnknownMemberType]
                        await pipe.execute()
                    pause = 0.0
                except (RedisError, OSError):
                    self.dropped += len(batch)
                    if not pause:
                        logger.warning(NOT_SENT, exc_info=True)
                    pause = _longer(pause)
                    await asyncio.sleep(pause)

    # Said once when a connection is lost or the first one fails, not on every try after.
    async def _listened(self) -> None:
        pause = 0.0
        while True:
            try:
                await self._heard()
            except (RedisError, OSError, TimeoutError):
                if not pause:
                    logger.warning(LOST, exc_info=True)
            pause = 0.0 if self.up else _longer(pause)
            await self._lost()
            await asyncio.sleep(pause)

    # Runs until the connection is lost: every message handed on, a ping when quiet, and the
    # channels nobody listens to any more let go.
    async def _heard(self) -> None:
        pubsub = self._pubsub = PubSub(self._listener.connection_pool)
        own = self.prefix + OWN.format(id=self.id)
        await pubsub.subscribe(own)
        self._reconnected = False
        if pubsub.connection is not None:
            pubsub.connection.register_connect_callback(self._connected_again)  # pyright: ignore[reportUnknownMemberType]
        clock = asyncio.get_running_loop().time
        heard_at = pinged_at = clock()
        while True:
            heard = _heard_of(await pubsub.get_message(timeout=TICK_S))  # pyright: ignore[reportUnknownArgumentType]
            now = clock()
            if heard is not None:
                heard_at = now
                self._handled(heard, own)
            if now - heard_at > SILENT_AT_MOST_S:
                raise RedisConnectionError(SILENT.format(seconds=now - heard_at))
            if self._reconnected:
                raise RedisConnectionError(BEHIND_OUR_BACK)
            if now - pinged_at >= PING_S:
                pinged_at = now
                await pubsub.execute_command("PING")
            leaving, self._leaving = self._leaving, set()
            for name in leaving - self._listening.keys():
                await pubsub.execute_command("UNSUBSCRIBE", name)

    # redis-py reconnects a connection a failed command was on, and subscribes it again: a
    # listener would then go on with a hole in what it heard, so that connection is given up.
    def _connected_again(self, _connection: object) -> None:
        self._reconnected = True

    def _handled(self, heard: Heard, own: str) -> None:
        if heard.kind == "subscribe" and heard.channel is not None:
            if heard.channel == own:
                self.up = True
                self._connected.set()
            confirmed = self._confirmed.get(heard.channel)
            if confirmed is not None and not confirmed.done():
                confirmed.set_result(True)
            return
        if heard.kind != "message" or heard.channel is None or heard.data is None:
            return
        for listening in tuple(self._listening.get(heard.channel, ())):
            if not listening.offer(heard.data):
                self._left(listening)

    # Every listener is dropped, not moved to the next connection: what was published between
    # the two is lost, and only the store gives it back.
    async def _lost(self) -> None:
        self.up = False
        self._connected.clear()
        listening, self._listening = self._listening, {}
        confirmed, self._confirmed = self._confirmed, {}
        self._leaving.clear()
        for waiting in confirmed.values():
            if not waiting.done():
                waiting.set_result(False)
        for listeners in listening.values():
            for listener in listeners:
                listener.drop()
        await self._forget_the_connection()

    async def _forget_the_connection(self) -> None:
        pubsub, self._pubsub = self._pubsub, None
        if pubsub is not None:
            await pubsub.aclose()

    def _left(self, listening: Listening) -> None:
        name = self.prefix + listening.channel
        listeners = self._listening.get(name)
        if listeners is None or listening not in listeners:
            return
        listeners.discard(listening)
        if not listeners:
            del self._listening[name]
            self._confirmed.pop(name, None)
            self._leaving.add(name)


type Signal = LocalSignal | RedisSignal


@asynccontextmanager
async def opened_signal(settings: Settings) -> AsyncGenerator[Signal]:
    """Redis at PINECALL_REDIS_URL, or this process alone when unset; closed after."""
    signal: Signal
    if settings.redis_url is None:
        signal = LocalSignal()
    else:
        signal = RedisSignal(settings.redis_url)
        signal.start()
    try:
        yield signal
    finally:
        await signal.close()


# Retries are ours: redis-py's would reconnect the listening connection behind our back.
def _client_of(url: str, name: str) -> Redis:
    try:
        return Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
            url,
            retry=Retry(NoBackoff(), 0),
            socket_connect_timeout=ANSWER_S,
            socket_timeout=ANSWER_S,
            client_name=f"pinecall-{name}",
        )
    except ValueError as wrong:
        raise SettingsRefused(NOT_A_URL.format(why=wrong)) from None


# redis-py hands its messages over as dicts it does not type.
def _heard_of(message: object) -> Heard | None:
    match message:
        case {"type": str() as kind, "channel": object() as channel, "data": object() as data}:
            name = channel.decode() if isinstance(channel, bytes) else None
            return Heard(kind, name, data if isinstance(data, bytes) else None)
        case _:
            return None


def _longer(pause: float) -> float:
    return FIRST_PAUSE_S if not pause else min(pause * 2, LONGEST_PAUSE_S)
