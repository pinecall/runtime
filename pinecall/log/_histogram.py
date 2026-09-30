"""A stage's seconds as a fixed histogram: counted in buckets, added across calls, read by rank."""

import math
from collections.abc import Iterable, Sequence

# Each bucket is 10 % wider than the one below it, from 10 ms to past a minute: a rank read off a
# bucket's geometric middle is within 5 % of the true value, and two histograms add bucket by
# bucket, which exact percentiles cannot.
SMALLEST_S = 0.01


GROWTH = 1.1


# Under 10 ms, then 92 buckets growing by GROWTH up to 63 s, then everything slower.
BUCKETS = 94


MEDIAN = 0.5


P95 = 0.95


# Bucket i (1 to BUCKETS - 2) holds [SMALLEST_S * GROWTH ** (i - 1), SMALLEST_S * GROWTH ** i).
UPPER: tuple[float, ...] = tuple(SMALLEST_S * GROWTH**index for index in range(BUCKETS - 1))


def empty() -> list[int]:
    """A histogram that counted nothing."""
    return [0] * BUCKETS


def counted(values: Iterable[float]) -> list[int]:
    """A histogram of these times; a negative one (livekit's -1 for none) is not counted."""
    buckets = empty()
    for seconds in values:
        if seconds >= 0:
            buckets[_bucket_of(seconds)] += 1
    return buckets


def added(first: Sequence[int], second: Sequence[int]) -> list[int]:
    """Two histograms as one."""
    return [mine + theirs for mine, theirs in zip(first, second, strict=True)]


# Nearest rank: the k-th smallest time, k = ceil(q * n), read as its bucket's middle.
def rank(buckets: Sequence[int], quantile: float) -> float | None:
    """The time at this quantile, within 5 %; None for a histogram that counted nothing."""
    total = sum(buckets)
    if total == 0:
        return None
    wanted = max(1, math.ceil(quantile * total))
    seen = 0
    for index, count in enumerate(buckets):
        seen += count
        if seen >= wanted:
            return _middle(index)
    return _middle(BUCKETS - 1)


def _middle(index: int) -> float:
    if index == 0:
        return SMALLEST_S / 2
    if index == BUCKETS - 1:
        return UPPER[-1]
    return math.sqrt(UPPER[index - 1] * UPPER[index])


def _bucket_of(seconds: float) -> int:
    if seconds < SMALLEST_S:
        return 0
    return min(BUCKETS - 1, 1 + math.floor(math.log(seconds / SMALLEST_S, GROWTH)))
