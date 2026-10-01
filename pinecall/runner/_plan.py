"""What a runner does this beat: decided from what the gateway wants and what podman has."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pinecall.runner._podman import Container
from pinecall.wire.rest.hosting import WantedApp

# From `podman run` to the gateway seeing the release's agents: past it, the release failed.
REGISTERS_WITHIN_S = 120.0


# A host that served and whose process exits is run again, this many times in this long; past
# it the release is failed, and said so.
MOST_CRASHES = 5


CRASH_WINDOW_S = 600.0


EXITED = "the process exited before its agents registered:\n{output}"


@dataclass(frozen=True)
class Start:
    """Install what is not installed and run the host wanted."""

    app: WantedApp


@dataclass(frozen=True)
class Revive:
    """A host that served and whose process exited: its last lines kept, and run again."""

    app: WantedApp
    container: Container


@dataclass(frozen=True)
class Stop:
    """A container nobody wants any more: drained and removed."""

    container: Container


@dataclass(frozen=True)
class WentLive:
    """The host wanted registered: said to the gateway, once."""

    app: WantedApp


@dataclass(frozen=True)
class Failed:
    """The host wanted did not start, register or stay up: said with its last lines."""

    app: WantedApp
    why: str
    container: Container


type Step = Start | Revive | Stop | WentLive | Failed


NEVER_REGISTERED = (
    f"no agent registered from this release within {REGISTERS_WITHIN_S:.0f}s:\n{{output}}"
)


KEEPS_EXITING = (
    f"the process exited {MOST_CRASHES} times in {CRASH_WINDOW_S / 60:.0f} minutes "
    "and is not run again:\n{output}"
)


def label_of(app: WantedApp) -> str:
    """What a container carries of whose it is: the org and the app."""
    return f"{app.org}/{app.name}"


def crashes_within(times: Sequence[float], now: float) -> list[float]:
    """The crashes still counted: those inside the window."""
    return [at for at in times if now - at < CRASH_WINDOW_S]


# Numbers in, steps out: nothing here touches podman or the gateway. The steps of one app are in
# the order they are to be done, and two apps' steps never depend on each other.
def planned(
    wanted: Sequence[WantedApp],
    containers: Sequence[Container],
    crashes: Mapping[str, Sequence[float]],
    now: float,
) -> dict[str, list[Step]]:
    """This beat's steps per app label; an app with nothing to do is not in it."""
    steps: dict[str, list[Step]] = {}
    for app in wanted:
        ours = [each for each in containers if each.app == label_of(app)]
        steps[label_of(app)] = _of_one(app, ours, crashes, now)
    labels = {label_of(app) for app in wanted}
    for container in containers:
        if container.app not in labels:
            steps.setdefault(container.app, []).append(Stop(container))
    return {label: todo for label, todo in steps.items() if todo}


# The host that last went live serves until the one wanted registers, whatever becomes of the
# one wanted; every other container of the app, and a host that failed, is removed.
def _of_one(
    app: WantedApp, ours: Sequence[Container], crashes: Mapping[str, Sequence[float]], now: float
) -> list[Step]:
    by_name = {each.name: each for each in ours}
    wanted = by_name.get(app.host)
    keep = {app.host, app.live_host}
    steps: list[Step] = []
    if app.failed:
        keep.discard(app.host)
    elif wanted is None:
        steps.append(Start(app))
    elif wanted.has_exited:
        steps.append(_exited(app, wanted, crashes, now))
    elif app.registered and app.live_host != app.host:
        steps.append(WentLive(app))
    elif not app.registered and _never_served_in_time(app, wanted, now):
        steps.append(Failed(app, NEVER_REGISTERED, wanted))
    if app.registered and not app.failed:
        keep = {app.host}
    before = by_name.get(app.live_host or "")
    if before is not None and before.name != app.host and before.has_exited:
        if before.name in keep and _may_run_again(before, crashes, now):
            steps.append(Revive(app, before))
        else:
            keep.discard(before.name)
    keep -= {step.container.name for step in steps if isinstance(step, Failed)}
    return steps + [Stop(each) for each in ours if each.name not in keep]


# One that never served is a release that does not work: failed at once. One that served is run
# again, until it has exited too often to be worth another try.
def _exited(
    app: WantedApp, container: Container, crashes: Mapping[str, Sequence[float]], now: float
) -> Step:
    if container.name != app.live_host:
        return Failed(app, EXITED, container)
    if not _may_run_again(container, crashes, now):
        return Failed(app, KEEPS_EXITING, container)
    return Revive(app, container)


def _may_run_again(
    container: Container, crashes: Mapping[str, Sequence[float]], now: float
) -> bool:
    return len(crashes_within(crashes.get(container.name, ()), now)) < MOST_CRASHES


# A host that served and is not registered this beat is between two sockets: left alone.
def _never_served_in_time(app: WantedApp, wanted: Container, now: float) -> bool:
    return app.live_host != app.host and now - wanted.started_at > REGISTERS_WITHIN_S
