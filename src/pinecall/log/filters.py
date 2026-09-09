"""What a reader asked to see: a set of types, durable only, and the four that always pass."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from pinecall._exceptions import PinecallError
from pinecall.log.entry import Entry
from pinecall_protocol.registry import TERMINAL_EVENT

# A reader names at most this many types. The number is not a performance limit; it is the point
# past which a filter is not a filter, and a query string that long came from a machine, not a hand.
MAX_TYPES = 32

# Every event type on the wire is lowercase words joined by dots. Anything else is a typo or an
# injection attempt, and both deserve the same answer: no.
_A_TYPE_NAME = re.compile(r"^[a-z0-9_.]+$")

# Four types a filter can never take away. Two say the stream itself lost something, and two say
# the call is over: a reader that filtered those out would wait forever for an entry that came
# and went. A filter narrows what a reader sees, never whether it learns the log ended.
ALWAYS_PASS: frozenset[str] = frozenset({"log.gap", "log.caught_up", "call.ended", TERMINAL_EVENT})


class FilterRefused(PinecallError):
    """A reader asked for something no filter may be: too many types, or a name off the charset."""


@dataclass(frozen=True)
class Filter:
    """One reader's narrowing. types None is every type; durable drops what a store may forget."""

    types: frozenset[str] | None = None
    durable: bool = False

    def passes(self, entry: Entry) -> bool:
        """Whether this entry reaches the reader. The always-pass set is checked first, and wins."""
        if entry.type in ALWAYS_PASS:
            return True
        if self.durable and entry.ephemeral:
            return False
        return self.types is None or entry.type in self.types

    @classmethod
    def of(cls, types: str | Iterable[str] | None = None, durable: bool = False) -> Filter:
        """Build one from what a query string carries: `types=a.b,c.d` and `durable=1`."""
        if types is None:
            return cls(types=None, durable=durable)
        names = types.split(",") if isinstance(types, str) else list(types)
        return cls(types=_a_set_of_type_names(names), durable=durable)


# The filter that narrows nothing: every reader starts here, and most stay.
EVERYTHING = Filter()


def _a_set_of_type_names(names: Iterable[str]) -> frozenset[str]:
    """Trim, drop the empties a trailing comma leaves, and refuse the rest by name."""
    wanted = [name.strip() for name in names]
    wanted = [name for name in wanted if name]
    if len(wanted) > MAX_TYPES:
        raise FilterRefused(f"a filter names at most {MAX_TYPES} types, not {len(wanted)}")
    if bad := [name for name in wanted if not _A_TYPE_NAME.match(name)]:
        raise FilterRefused(f"an event type is lowercase words joined by dots: {bad}")
    return frozenset(wanted)
