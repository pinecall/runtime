"""What a process counts as it works, in memory, and the Prometheus text it is read as."""

from bisect import bisect_left
from collections import Counter, deque
from collections.abc import Iterable, Mapping
from typing import Literal

# Seconds: an append waits on one Postgres transaction, so the buckets are a transaction's.
APPEND_BOUNDS_S = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

# A stage's fallbacks are ordered by the last two minutes of each vendor: judged once five calls
# were handed it as a stage's first, and over the line once half of those calls saw it fail.
VENDOR_WINDOW_S = 120.0
CALLS_TO_JUDGE = 5
FAILED_SHARE = 0.5
# A busy window is thousands of calls: past this many, a vendor's oldest are forgotten early.
MOST_KEPT = 10_000


type Rows = Iterable[tuple[Mapping[str, str], float]]

type Kind = Literal["gauge", "counter"]


class Histogram:
    """Observations counted into fixed buckets, with their sum, as Prometheus reads one."""

    def __init__(self, bounds: tuple[float, ...]) -> None:
        """Every bucket empty."""
        self.bounds = bounds
        self.counts = [0] * (len(bounds) + 1)
        self.total = 0.0

    def observe(self, value: float) -> None:
        """Count one observation in the first bucket whose bound holds it."""
        self.counts[bisect_left(self.bounds, value)] += 1
        self.total += value


# Called on the append path: a bisect and three additions, no lock (one event loop).
class Counters:
    """The gateway's counts since it started: appends, their time, and the errors workers wrote."""

    def __init__(self) -> None:
        """Nothing counted yet."""
        self.append_seconds = Histogram(APPEND_BOUNDS_S)
        self.appended = 0
        # By the entry's code and the vendor whose plugin failed, "" when none did.
        self.errors: Counter[tuple[str, str]] = Counter()
        # By vendor: when a call was handed it first for a stage, and when a call saw it fail.
        self.handed: dict[str, deque[float]] = {}
        self.failures: dict[str, deque[tuple[float, str]]] = {}
        # By org: how many times its usual day today's spend is, while it is unusual.
        self.unusual: dict[str, float] = {}

    def appended_in(self, seconds: float, entries: int) -> None:
        """One append at the door: how long it took and how many entries it wrote."""
        self.append_seconds.observe(seconds)
        self.appended += entries

    def failed(self, code: str, vendor: str) -> None:
        """One error entry a worker wrote."""
        self.errors[(code, vendor)] += 1

    def failed_on(self, vendor: str, call: str, at: float) -> None:
        """A call saw the vendor fail: an error naming it, or its stage's switch away from it."""
        self.failures.setdefault(vendor, deque(maxlen=MOST_KEPT)).append((at, call))

    def handed_out(self, vendors: Iterable[str], at: float) -> None:
        """A call's stages were resolved with these vendors first, each counted once."""
        for vendor in set(vendors):
            self.handed.setdefault(vendor, deque(maxlen=MOST_KEPT)).append(at)

    def spending(self, org: str, multiple: float | None) -> None:
        """What the last seal found of the org's spend: unusual by this much, or None, usual."""
        if multiple is None:
            self.unusual.pop(org, None)
        else:
            self.unusual[org] = multiple

    def failing(self, at: float) -> frozenset[str]:
        """The vendors over their error line in the window up to now."""
        since = at - VENDOR_WINDOW_S
        for failures in self.failures.values():
            while failures and failures[0][0] < since:
                failures.popleft()
        over: set[str] = set()
        for vendor, handed in self.handed.items():
            while handed and handed[0] < since:
                handed.popleft()
            calls = len({call for _, call in self.failures.get(vendor, ())})
            if len(handed) >= CALLS_TO_JUDGE and calls >= FAILED_SHARE * len(handed):
                over.add(vendor)
        return frozenset(over)


def family(name: str, what: str, kind: Kind, rows: Rows) -> str:
    """One gauge or counter family, a line per label set, with its help and type lines."""
    lines = [f"# HELP {name} {what}", f"# TYPE {name} {kind}"]
    lines += [f"{name}{_labelled(labels)} {_number(value)}" for labels, value in rows]
    return "\n".join(lines) + "\n"


def histogram(name: str, what: str, observed: Histogram) -> str:
    """One histogram: its cumulative buckets, the sum and the count."""
    lines = [f"# HELP {name} {what}", f"# TYPE {name} histogram"]
    running = 0
    for bound, count in zip((*observed.bounds, float("inf")), observed.counts, strict=True):
        running += count
        lines.append(f'{name}_bucket{{le="{_number(bound)}"}} {running}')
    lines += [f"{name}_sum {_number(observed.total)}", f"{name}_count {running}"]
    return "\n".join(lines) + "\n"


# The text format's three escapes in a label value: backslash, quote, newline.
def _labelled(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    pairs = (
        f'{key}="{value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")}"'
        for key, value in labels.items()
    )
    return "{" + ",".join(pairs) + "}"


def _number(value: float) -> str:
    return "+Inf" if value == float("inf") else repr(float(value))
