"""Who runs each written call and holds each WhatsApp thread, across the gateways of a box."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from pinecall.process.shared import Shared
from pinecall.process.signal import Signal

# Each gateway says the written calls it runs and the threads it holds here, merged by each.
OWNED_CHANNEL = "owned"

# Where a gateway hands a WhatsApp message on to the one holding its contact's thread.
THREAD_CHANNEL = "thread:{gateway}"


@dataclass(frozen=True)
class Owned:
    """One gateway's share: the written calls whose sessions run there, the threads it holds."""

    texts: tuple[str, ...] = ()
    threads: tuple[str, ...] = ()


# A session and a thread live where they were opened: another gateway takes one up only once its
# owner fell silent, when the owner is forgotten (process/shared.py).
class Owners:
    """This gateway's written calls and threads, and every other gateway's as it says them."""

    def __init__(self, signal: Signal) -> None:
        """Nothing run and nothing held yet."""
        self.shared = Shared(signal, OWNED_CHANNEL, _OWNED, Owned(), self._merged)
        self.texts: set[str] = set()
        self.threads: set[str] = set()
        self._texts_elsewhere: set[str] = set()
        # Each thread another live gateway holds, and that gateway's name on the signal.
        self.threads_elsewhere: dict[str, str] = {}

    @property
    def id(self) -> str:
        """This gateway's name on the signal."""
        return self.shared.id

    async def start(self) -> None:
        """Hear the other gateways, and say what runs here."""
        await self.shared.start()

    async def close(self) -> None:
        """Stop hearing and saying."""
        await self.shared.close()

    def running(self, call: str, *, here: bool) -> None:
        """A written call's session started here, or ended."""
        if (call in self.texts) == here:
            return
        if here:
            self.texts.add(call)
        else:
            self.texts.discard(call)
        self._say()

    def holding(self, thread: str, *, here: bool) -> None:
        """A contact's thread opened here, or closed."""
        if (thread in self.threads) == here:
            return
        if here:
            self.threads.add(thread)
        else:
            self.threads.discard(thread)
        self._say()

    def text_elsewhere(self, call: str) -> bool:
        """Whether a live gateway other than this one runs the written call."""
        return call in self._texts_elsewhere

    def _say(self) -> None:
        self.shared.put(Owned(texts=tuple(sorted(self.texts)), threads=tuple(sorted(self.threads))))

    def _merged(self) -> None:
        heard = self.shared.theirs.items()
        self._texts_elsewhere = {call for _, share in heard for call in share.share.texts}
        self.threads_elsewhere = {
            thread: gateway for gateway, share in heard for thread in share.share.threads
        }


_OWNED: TypeAdapter[Owned] = TypeAdapter(Owned)
