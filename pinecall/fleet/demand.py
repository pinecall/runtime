"""How many scaled workers a fleet wants: the one rule Kubernetes sizes the burst by."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from pinecall.fleet.roster import heard_lately
from pinecall.wire.rest.fleet import WorkerStatus


@dataclass(frozen=True)
class Line:
    """Where a fleet is kept: the busy target, the slack under it, how many, and their seats."""

    # Grow above `target`; shrink only below `target - slack`, so two asks do not oscillate.
    target: float = 0.6
    slack: float = 0.15
    at_least: int = 0
    at_most: int = 10
    # PINECALL_MAX_JOBS of a scaled worker: what one more of them adds.
    seats_per_worker: int = 4


# A cluster's workers that scale (their names start with `scaled`) are sized by Kubernetes, which
# asks this number, counting the seats of the workers that do not scale (a core node's) first, so
# a call they hold asks for no scaled one. Grow to what brings busy under the target; let one go
# only when busy stays under target - slack without it; else hold. Absolute, so a pod still
# booting, not yet heard, is never asked for twice.
def wanted_scaled(
    seats: Sequence[WorkerStatus], scaled: str, line: Line, now: float
) -> tuple[int, int, int]:
    """How many scaled workers the fleet wants now, its calls, and the seats it holds."""
    counted = [seat for seat in seats if heard_lately(seat, now) and seat.max_jobs is not None]
    holding = [seat for seat in counted if not seat.cordoned]
    active = sum(seat.active for seat in counted)
    fixed = sum(seat.max_jobs or 0 for seat in holding if not seat.worker.startswith(scaled))
    current = sum(1 for seat in holding if seat.worker.startswith(scaled))
    each = line.seats_per_worker
    # The target's decimal as written: 6 / 0.6 in floats is 10.000000000000002, one seat too many.
    needed = math.ceil(Fraction(active) / Fraction(str(line.target))) - fixed
    grown = max(0, math.ceil(Fraction(needed) / each)) if needed > 0 else 0
    capacity = fixed + current * each
    if grown > current:
        return min(grown, line.at_most), active, capacity
    left = fixed + (current - 1) * each
    # With no call one may always go; with calls, only if the seats left keep busy under the slack.
    quiet = active == 0 or (left > 0 and active / left < line.target - line.slack)
    if current > max(grown, line.at_least) and quiet:
        return current - 1, active, capacity
    return current, active, capacity
