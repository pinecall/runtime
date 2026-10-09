"""The lexicon read back: the voice's spoken forms become, in the transcript, the words written."""

import re
from collections.abc import AsyncIterable, AsyncIterator, Mapping, Sequence

from livekit.agents.types import NOT_GIVEN, TimedString


class Unsaying:
    """The lexicon's spoken forms, matched as livekit's own replace matched the written words."""

    def __init__(self, says: Mapping[str, str]) -> None:
        """Every spoken form, longest first, mapped back to the word written."""
        self.written = {spoken.lower(): word for word, spoken in says.items() if spoken}
        keys = sorted(self.written, key=len, reverse=True)
        self.pattern = re.compile("|".join(re.escape(key) for key in keys), re.IGNORECASE)
        prefixes = {key[:n] for key in keys for n in range(1, len(key))}
        self.started = (
            re.compile("(?:" + "|".join(re.escape(p) for p in prefixes) + r")\Z", re.IGNORECASE)
            if prefixes
            else None
        )

    def back(self, text: str) -> str:
        """The text with every spoken form put back as the word written."""
        return self.pattern.sub(lambda found: self.written[found.group(0).lower()], text)

    def may_go_on(self, text: str) -> bool:
        """Whether the text ends in what the next piece could complete into a spoken form."""
        return self.started is not None and self.started.search(text) is not None

    def found(self, text: str) -> bool:
        """Whether the text holds a spoken form."""
        return self.pattern.search(text) is not None


async def unsaid(
    pieces: AsyncIterable[str | TimedString], says: Mapping[str, str]
) -> AsyncIterator[str | TimedString]:
    """The transcript pieces with the lexicon's spoken forms read back as the words written."""
    if not says:
        async for piece in pieces:
            yield piece
        return
    unsaying = Unsaying(says)
    pending: list[str | TimedString] = []
    async for piece in pieces:
        pending.append(piece)
        text = "".join(pending)
        if unsaying.found(text) and not unsaying.may_go_on(text):
            yield _joined(pending, unsaying.back(text))
            pending = []
        elif not unsaying.may_go_on(text):
            for piece_kept in pending:
                yield piece_kept
            pending = []
    if pending:
        text = "".join(pending)
        yield (
            _joined(pending, unsaying.back(text))
            if unsaying.found(text)
            else _joined(pending, text)
        )


def _joined(pending: Sequence[str | TimedString], text: str) -> str | TimedString:
    first, last = pending[0], pending[-1]
    if not isinstance(first, TimedString) or not isinstance(last, TimedString):
        return text
    return TimedString(
        text,
        start_time=first.start_time,
        end_time=last.end_time if last.end_time is not NOT_GIVEN else first.end_time,
    )
