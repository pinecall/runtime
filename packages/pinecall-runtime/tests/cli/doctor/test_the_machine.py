"""The three lines about the machine itself: room on the disk, the fence, the certificate."""

from datetime import UTC, datetime, timedelta

import pytest

from pinecall.cli.doctor import machine
from pinecall.cli.doctor import verbs as doctor
from pinecall.settings import load_settings
from tests.cli.doctor.reading import named, probes_that_answer

pytestmark = pytest.mark.unit


def test_a_disk_with_room_says_how_much_and_where() -> None:
    line = named("disk", doctor.run_checks(load_settings(), probes_that_answer()))
    assert line.ok and "100.0 GB free under" in line.detail


def test_a_disk_under_the_floor_is_the_verdict_and_says_what_it_costs() -> None:
    results = doctor.run_checks(load_settings(), probes_that_answer(disk_free_gb=lambda _p: 1.5))
    down = doctor.first_failure(results)
    assert down is not None and down.name == "disk"
    assert machine.A_FULL_DISK in down.detail and "1.5 GB" in down.detail


def test_the_disk_measured_is_the_one_the_recordings_land_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_RECORDINGS", "/var/lib/pinecall/recordings/sandbox")
    asked: list[str] = []

    def measure(path: str) -> float:
        asked.append(path)
        return 50.0

    doctor.run_checks(load_settings(), probes_that_answer(disk_free_gb=measure))
    assert asked == ["/var/lib/pinecall/recordings/sandbox"]


def test_a_fence_that_is_down_is_the_verdict_and_names_what_is_open() -> None:
    results = doctor.run_checks(load_settings(), probes_that_answer(unit_active=lambda _u: False))
    down = doctor.first_failure(results)
    assert down is not None and down.name == "fence"
    assert machine.NO_FENCE in down.detail


def test_a_machine_with_no_systemd_has_nothing_to_fence() -> None:
    line = named(
        "fence", doctor.run_checks(load_settings(), probes_that_answer(unit_active=lambda _u: None))
    )
    assert line.ok and line.detail == machine.NO_SYSTEMD


def test_the_fence_asked_after_is_nftables() -> None:
    asked: list[str] = []

    def is_active(unit: str) -> bool:
        asked.append(unit)
        return True

    doctor.run_checks(load_settings(), probes_that_answer(unit_active=is_active))
    assert asked == [machine.THE_FENCE]


def test_a_laptop_with_no_domain_has_no_certificate_to_read() -> None:
    line = named("certificate", doctor.run_checks(load_settings(), probes_that_answer()))
    assert line.ok and line.detail == machine.NO_DOMAIN


def test_a_certificate_with_a_year_left_says_when_it_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINECALL_DOMAIN", "box.example.com")
    line = named("certificate", doctor.run_checks(load_settings(), probes_that_answer()))
    assert line.ok and line.detail.startswith("box.example.com — ends 2100-01-01")


def test_a_certificate_inside_two_weeks_is_the_verdict_and_points_at_caddy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_DOMAIN", "box.example.com")
    soon = datetime.now(UTC) + timedelta(days=5)
    results = doctor.run_checks(
        load_settings(), probes_that_answer(certificate_expiry=lambda _d: soon)
    )
    down = doctor.first_failure(results)
    assert down is not None and down.name == "certificate"
    assert "in 4 days" in down.detail or "in 5 days" in down.detail
    assert machine.NOT_RENEWED in down.detail


def test_a_domain_that_does_not_answer_is_named_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_DOMAIN", "box.example.com")

    def refuse(_domain: str) -> datetime:
        raise ConnectionRefusedError("[Errno 61] Connection refused")

    results = doctor.run_checks(load_settings(), probes_that_answer(certificate_expiry=refuse))
    down = doctor.first_failure(results)
    assert down is not None and down.name == "certificate"
    assert "Connection refused" in down.detail


def test_a_worker_is_not_asked_after_a_certificate_it_does_not_serve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_ROLE", "worker")
    names = [result.name for result in doctor.run_checks(load_settings(), probes_that_answer())]
    assert "certificate" not in names and "disk" in names and "fence" in names
