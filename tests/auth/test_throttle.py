"""So many knocks per name per window, counted right or wrong, and the window slides."""

import pytest

from pinecall.auth.throttle import Throttle

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_the_sixth_knock_in_a_minute_is_refused_and_another_name_is_not() -> None:
    throttle = Throttle(tries=5, window_s=60, clock=_Clock())
    assert all(throttle.allowed("berna@clinica") for _ in range(5))
    assert not throttle.allowed("berna@clinica")
    assert throttle.allowed("ana@clinica")


def test_the_window_slides_so_a_minute_later_the_name_knocks_again() -> None:
    clock = _Clock()
    throttle = Throttle(tries=2, window_s=60, clock=clock)
    assert throttle.allowed("berna") and throttle.allowed("berna")
    assert not throttle.allowed("berna")
    clock.now = 61.0
    assert throttle.allowed("berna")
