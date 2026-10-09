"""A monitor: one number of the series watched over a window, and the line it must not cross."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from pinecall.domain.errors import DeclarationRefused

WINDOWS = (1, 7, 30)
NOT_A_METRIC = "a monitor watches one of {metrics}, not {said!r}"
NOT_A_WINDOW = "a monitor's window is 1, 7 or 30 days, not {said}"
NO_NAME = "a monitor has a name"
A_SHARE = "{metric} is a share between 0 and 1, not {said}"
A_RATE = frozenset({"held_rate", "escalated_rate", "tool_failure_rate"})
type Metric = Literal[
    "e2e_median_s",
    "llm_median_s",
    "held_rate",
    "escalated_rate",
    "tool_failure_rate",
    "spend_usd",
    "calls",
]


@dataclass(frozen=True)
class Monitor:
    """What is watched, where the line is, and when it last fired."""

    id: str
    name: str
    metric: Metric
    above: bool
    threshold: float
    window_days: int
    agent: str | None = None
    created_by: str = ""
    fired_on: date | None = None
    fired_value: float | None = None

    def __post_init__(self) -> None:
        if self.metric not in METRICS:
            named = ", ".join(METRICS)
            raise DeclarationRefused(NOT_A_METRIC.format(metrics=named, said=self.metric))
        if self.window_days not in WINDOWS:
            raise DeclarationRefused(NOT_A_WINDOW.format(said=self.window_days))
        if not self.name.strip():
            raise DeclarationRefused(NO_NAME)
        if self.metric in A_RATE and not 0 <= self.threshold <= 1:
            raise DeclarationRefused(A_SHARE.format(metric=self.metric, said=self.threshold))

    def crossed(self, value: float) -> bool:
        """Whether the value is on the wrong side of the line."""
        return value > self.threshold if self.above else value < self.threshold


METRICS: tuple[Metric, ...] = (
    "e2e_median_s",
    "llm_median_s",
    "held_rate",
    "escalated_rate",
    "tool_failure_rate",
    "spend_usd",
    "calls",
)
