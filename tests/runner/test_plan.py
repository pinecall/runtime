"""Tests for the runner's plan: what one beat does to an app, from numbers alone."""

from pinecall.runner._plan import (
    CRASH_WINDOW_S,
    MOST_CRASHES,
    REGISTERS_WITHIN_S,
    Failed,
    Revive,
    Start,
    Stop,
    WentLive,
    crashes_within,
    planned,
)
from pinecall.runner._podman import Container
from pinecall.wire.rest.hosting import WantedApp

NOW = 10_000.0


def wanted(
    host: str = "support-r2-bbbbbbbb",
    release: int = 2,
    *,
    live_host: str | None = "support-r1-aaaaaaaa",
    registered: bool = False,
    failed: bool = False,
) -> WantedApp:
    return WantedApp(
        org="org_1",
        name="support",
        release=release,
        sha256="0" * 64,
        host=host,
        registered=registered,
        failed=failed,
        logs_wanted=False,
        live_host=live_host,
    )


def container(name: str, state: str = "running", started_at: float = NOW) -> Container:
    return Container(
        name=name,
        app="org_1/support",
        release=int(name.split("-r")[1][0]),
        state=state,
        started_at=started_at,
    )


OLD = container("support-r1-aaaaaaaa")


NEW = container("support-r2-bbbbbbbb")


def test_an_app_with_no_container_is_started_and_one_nobody_wants_is_stopped() -> None:
    stray = Container(name="x-r1-cccccccc", app="org_2/x", release=1, state="running", started_at=0)
    assert planned([wanted(live_host=None)], [stray], {}, NOW) == {
        "org_1/support": [Start(wanted(live_host=None))],
        "org_2/x": [Stop(stray)],
    }


def test_the_old_host_serves_until_the_new_registers_then_goes() -> None:
    assert planned([wanted()], [OLD, NEW], {}, NOW) == {}
    live = wanted(registered=True)
    assert planned([live], [OLD, NEW], {}, NOW) == {"org_1/support": [WentLive(live), Stop(OLD)]}
    settled = wanted(registered=True, live_host=NEW.name)
    assert planned([settled], [NEW], {}, NOW) == {}


def test_a_new_host_that_exits_before_registering_failed_and_the_old_one_stays() -> None:
    exited = container(NEW.name, "exited")
    [step, *rest] = planned([wanted()], [OLD, exited], {}, NOW)["org_1/support"]
    assert isinstance(step, Failed)
    assert step.container == exited
    assert "exited before its agents registered" in step.why
    assert rest == [Stop(exited)]


def test_a_new_host_that_never_registers_in_time_failed_and_between_sockets_is_left() -> None:
    late = container(NEW.name, started_at=NOW - REGISTERS_WITHIN_S - 1)
    [step, stop] = planned([wanted()], [OLD, late], {}, NOW)["org_1/support"]
    assert isinstance(step, Failed)
    assert "no agent registered" in step.why
    assert stop == Stop(late)
    assert planned([wanted()], [OLD, container(NEW.name, started_at=NOW - 1)], {}, NOW) == {}
    # The live host, not registered this beat, is between two sockets: nothing is done to it.
    settled = wanted(host=OLD.name, release=1, live_host=OLD.name)
    assert planned([settled], [container(OLD.name, started_at=0)], {}, NOW) == {}


def test_a_failed_host_is_stopped_and_the_live_one_kept() -> None:
    assert planned([wanted(failed=True)], [OLD, NEW], {}, NOW) == {"org_1/support": [Stop(NEW)]}


def test_a_live_host_that_exits_is_run_again_until_it_has_exited_too_often() -> None:
    settled = wanted(host=OLD.name, release=1, live_host=OLD.name)
    gone = container(OLD.name, "exited")
    assert planned([settled], [gone], {}, NOW) == {"org_1/support": [Revive(settled, gone)]}
    crashes = {OLD.name: [NOW - 10 * each for each in range(MOST_CRASHES)]}
    [step, stop] = planned([settled], [gone], crashes, NOW)["org_1/support"]
    assert isinstance(step, Failed)
    assert "not run again" in step.why
    assert stop == Stop(gone)
    old = {OLD.name: [NOW - CRASH_WINDOW_S - 1] * MOST_CRASHES}
    assert planned([settled], [gone], old, NOW) == {"org_1/support": [Revive(settled, gone)]}


def test_the_old_host_exiting_while_the_new_installs_is_run_again_too() -> None:
    gone = container(OLD.name, "exited")
    assert planned([wanted()], [gone], {}, NOW) == {
        "org_1/support": [Start(wanted()), Revive(wanted(), gone)]
    }


def test_crashes_outside_the_window_are_forgotten() -> None:
    assert crashes_within([NOW - CRASH_WINDOW_S, NOW - 1], NOW) == [NOW - 1]
