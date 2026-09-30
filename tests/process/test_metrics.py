"""Tests for the counters and their Prometheus text: cumulative buckets, labels escaped."""

from pinecall.process.metrics import Counters, Histogram, family, histogram


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
