"""What one gateway sends an app socket another holds, the answers back, and who holds which."""

from dataclasses import dataclass

from pydantic import BaseModel, TypeAdapter

from pinecall.domain.errors import NotAvailable
from pinecall.domain.scope import Scope
from pinecall.process.shared import Shared
from pinecall.process.signal import Signal
from pinecall.wire.commands import DevAnswer
from pinecall.wire.frames import Entry

# Each app socket's inbox, listened to by the gateway holding it while it is open.
APP_CHANNEL = "app:{app}"

# Where a dev.request's answer goes back to the gateway that asked it.
ANSWER_CHANNEL = "answer:{id}"

# Every gateway says the app sockets it holds here, for the org's list of apps.
SOCKETS_CHANNEL = "sockets"


class Bound(BaseModel):
    """A call bound to an app socket another gateway holds, and the seq its pump starts after."""

    call: str
    after: int


class ForApp(BaseModel):
    """One message for an app socket: a call bound to it, an entry to send it, or a stop."""

    bound: Bound | None = None
    entry: Entry | None = None
    stop: str | None = None


@dataclass(frozen=True)
class SocketRow:
    """An app socket another gateway holds, as the org's list of apps shows it."""

    app: str
    scope: Scope
    address: str | None
    connected_at: float
    host: str | None = None


class Elsewhere:
    """The app sockets the other gateways hold, as each says them."""

    def __init__(self, signal: Signal) -> None:
        """Nothing heard yet."""
        self.shared = Shared(signal, SOCKETS_CHANNEL, _ROWS, (), lambda: None)

    async def start(self) -> None:
        """Hear the others, and say this gateway's."""
        await self.shared.start()

    async def close(self) -> None:
        """Stop hearing and saying."""
        await self.shared.close()

    def put(self, rows: tuple[SocketRow, ...]) -> None:
        """This gateway's sockets changed."""
        self.shared.put(rows)

    def rows_of(self, org: str, env: str) -> list[SocketRow]:
        """The org's sockets in the world on every other gateway."""
        return [
            row
            for heard in self.shared.theirs.values()
            for row in heard.share
            if row.scope.org == org and row.scope.env == env
        ]


_ROWS: TypeAdapter[tuple[SocketRow, ...]] = TypeAdapter(tuple[SocketRow, ...])


# Lossy, as the signal is: a bound call whose notice was lost is told again when its worker asks
# a tool, and parked calls are handed on at every pass.
def told_bound(signal: Signal, app: str, bound: Bound) -> None:
    """Tell the gateway holding the socket that the call is bound to it, from this seq."""
    signal.publish(APP_CHANNEL.format(app=app), ForApp(bound=bound).model_dump_json().encode())


async def told_app(signal: Signal, app: str, message: ForApp) -> bool:
    """Hand the message to the gateway holding the socket; False when none does."""
    channel = APP_CHANNEL.format(app=app)
    try:
        return await signal.published(channel, message.model_dump_json().encode()) > 0
    except NotAvailable:
        return False


async def answered_back(signal: Signal, answer: DevAnswer) -> bool:
    """Hand a dev.answer to the gateway that asked it; False when none waits."""
    channel = ANSWER_CHANNEL.format(id=answer.id)
    try:
        return await signal.published(channel, answer.model_dump_json().encode()) > 0
    except NotAvailable:
        return False
