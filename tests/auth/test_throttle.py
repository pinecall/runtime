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


def test_names_that_stopped_knocking_are_forgotten_so_a_script_cannot_fill_the_table() -> None:
    """A million addresses tried once each is a million entries nobody knocks on again."""
    clock = _Clock()
    throttle = Throttle(tries=5, window_s=60, clock=clock, sweep_at=100)
    for stranger in range(100):
        assert throttle.allowed(f"stranger{stranger}@nowhere")
    assert throttle.names == 100
    clock.now = 61.0
    assert throttle.allowed("berna@clinica")
    assert throttle.names == 1, "the hundred quiet names went, and the one knocking stayed"
    clock.now = 90.0
    assert throttle.allowed("berna@clinica")
    assert throttle.names == 1


def test_a_sweep_keeps_every_name_still_within_its_window() -> None:
    clock = _Clock()
    throttle = Throttle(tries=5, window_s=60, clock=clock, sweep_at=2)
    assert throttle.allowed("berna") and throttle.allowed("ana")
    clock.now = 30.0
    assert throttle.allowed("luis")
    assert throttle.names == 3
