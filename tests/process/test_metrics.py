"""Tests for the counters and their Prometheus text: cumulative buckets, labels escaped."""

import math

from pinecall.process.metrics import (
    CALLS_TO_JUDGE,
    FAILED_SHARE,
    VENDOR_WINDOW_S,
    Counters,
    Histogram,
    family,
    histogram,
)


def test_a_histogram_is_written_cumulative_with_its_sum_and_count() -> None:
    observed = Histogram((0.01, 0.1))
    for seconds in (0.005, 0.05, 0.05, 3.0):
        observed.observe(seconds)
    assert histogram("x_seconds", "How long.", observed).splitlines() == [
        "# HELP x_seconds How long.",
        "# TYPE x_seconds histogram",
        'x_seconds_bucket{le="0.01"} 1',
        'x_seconds_bucket{le="0.1"} 3',
        'x_seconds_bucket{le="+Inf"} 4',
        "x_seconds_sum 3.105",
        "x_seconds_count 4",
    ]


def test_a_family_has_a_line_per_label_set_and_escapes_what_the_format_asks() -> None:
    rows: list[tuple[dict[str, str], float]] = [({}, 2), ({"code": 'a"b\\c\nd'}, 1.5)]
    assert family("x_total", "Things.", "counter", rows).splitlines() == [
        "# HELP x_total Things.",
        "# TYPE x_total counter",
        "x_total 2.0",
        'x_total{code="a\\"b\\\\c\\nd"} 1.5',
    ]


def test_the_counters_add_up_appends_and_errors_by_code_and_vendor() -> None:
    counted = Counters()
    counted.appended_in(0.002, 64)
    counted.appended_in(0.2, 1)
    counted.failed("component_failed", "deepgram")
    counted.failed("component_failed", "deepgram")
    assert counted.appended == 65
    assert sum(counted.append_seconds.counts) == 2
    assert counted.errors == {("component_failed", "deepgram"): 2}


def handed(counters: Counters, vendor: str, calls: int, at: float = 0.0) -> None:
    """This many calls resolved with the vendor first for a stage."""
    for _ in range(calls):
        counters.handed_out([vendor], at)


def test_a_vendor_half_of_whose_calls_saw_it_fail_is_over_its_line() -> None:
    counters = Counters()
    handed(counters, "deepgram", CALLS_TO_JUDGE + 1)
    handed(counters, "soniox", CALLS_TO_JUDGE + 1)
    for call in range(math.ceil(FAILED_SHARE * (CALLS_TO_JUDGE + 1))):
        counters.failed_on("deepgram", f"call_{call}", 1.0)
        counters.failed_on("deepgram", f"call_{call}", 1.5)
    counters.failed_on("soniox", "call_9", 1.0)
    assert counters.failing(2.0) == {"deepgram"}


def test_a_few_calls_say_nothing_and_every_failure_of_one_call_is_one_call() -> None:
    counters = Counters()
    handed(counters, "deepgram", CALLS_TO_JUDGE - 1)
    for call in range(CALLS_TO_JUDGE - 1):
        counters.failed_on("deepgram", f"call_{call}", 1.0)
    assert counters.failing(2.0) == frozenset()
    handed(counters, "deepgram", 1)
    many = Counters()
    handed(many, "deepgram", 10)
    for _ in range(10):
        many.failed_on("deepgram", "call_1", 1.0)
    assert many.failing(2.0) == frozenset()


# Once last, a vendor is handed no call and fails none: the window forgets it, and it is back.
def test_a_vendor_is_back_in_its_place_once_its_failures_leave_the_window() -> None:
    counters = Counters()
    handed(counters, "deepgram", CALLS_TO_JUDGE)
    for call in range(CALLS_TO_JUDGE):
        counters.failed_on("deepgram", f"call_{call}", 0.0)
    assert counters.failing(1.0) == {"deepgram"}
    assert counters.failing(VENDOR_WINDOW_S + 1) == frozenset()


def test_a_vendor_never_handed_out_is_never_over_its_line() -> None:
    counters = Counters()
    counters.failed_on("hume", "call_1", 0.0)
    assert counters.failing(1.0) == frozenset()
