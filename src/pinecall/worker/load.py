"""What a worker reports as its load, and the line livekit-server stops routing to it at."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import MISSING, fields
from typing import cast

from livekit.agents import AgentServer, WorkerOptions

logger = logging.getLogger(__name__)

# livekit-server hands a job only to a worker whose REPORTED load is under its target load: a
# fleet's affinity is `sum(max(0, targetLoad - w.Load()))` over its workers (livekit-server 1.13.6,
# pkg/service/agentservice.go, JobRequestAffinity), and target_load is 0.7 unless livekit.yaml says
# otherwise (pkg/agent/config.go, `const DefaultTargetLoad = 0.7`). At or over it the affinity is
# zero, the dispatch dies with `no servers available (received 1 responses)`, and the worker stays
# registered, healthy and silent. docs/decisions/worker.md.
REFUSED_AT = 0.7

# What a worker whose gate is not the machine reports: none. `dev` reports this, because dev mode
# already refuses no job on load (agents/worker.py:148) — it just never said so to the server.
NO_LOAD = 0.0


def reports_no_load() -> float:
    """The load a worker off the machine's gate reports: none, which is what dev mode means."""
    return NO_LOAD


# livekit's own calculator, read off the PUBLIC surface that carries it: it is the default value of
# the `load_fnc` field of WorkerOptions (agents/worker.py:188, exported by livekit.agents). The
# calculator itself is a private class of livekit's, and naming it here would go stale in silence
# the day livekit renames it; a public option that moves is a type error, read out loud.
def livekits_own_measure() -> Callable[[AgentServer], float]:
    """What a worker reports when it is handed no load_fnc: the machine's CPU average over 2.5s."""
    (declared,) = (field for field in fields(WorkerOptions) if field.name == "load_fnc")
    if declared.default is MISSING:
        raise RuntimeError("livekit's load_fnc option no longer carries a calculator of its own")
    # A dataclass field's default is `Any` by construction; the check above is what makes it a
    # calculator, and the cast says so instead of letting `Any` leak into the caller.
    return cast("Callable[[AgentServer], float]", declared.default)


class SlotLoad:
    """Calls held over calls this worker may hold — what livekit routes on, on a box of its own."""

    # A worker on a box of its own has no neighbours to measure, and a fleet is summed in slots
    # and never in percent: `free = Σ(max − active)` is a number a person and a loop both read.
    # livekit re-reads this every 0.5 s and two jobs inside that window both see the old count,
    # so `max_jobs` is one under the measured ceiling and the tolerance is one call, never more.
    def __init__(self, max_jobs: int) -> None:
        if max_jobs < 1:
            raise ValueError(f"a worker holds at least one call: max_jobs={max_jobs}")
        self.max_jobs = max_jobs
        self._refused = False

    def __call__(self, server: AgentServer) -> float:
        """The fraction of this worker's slots in use, with a line whenever it crosses the gate."""
        load = len(server.active_jobs) / self.max_jobs
        refused = load >= REFUSED_AT
        if refused != self._refused:
            self._refused = refused
            self._say_what_changed(len(server.active_jobs), refused)
        return load

    def _say_what_changed(self, active: int, refused: bool) -> None:
        """Which call count took this worker off the dispatcher, or put it back."""
        if refused:
            logger.warning(
                "%d of %d slots held: livekit will route no job to this worker until one ends",
                active,
                self.max_jobs,
            )
        else:
            logger.info(
                "%d of %d slots held: livekit is routing jobs here again", active, self.max_jobs
            )


class MachineLoad:
    """livekit's own CPU average, said out loud each time it crosses livekit-server's line."""

    # One per AgentServer, never a module-level flag: one process, one fleet, one crossing to
    # remember. `measuring` defaults to livekit's own calculator; a test hands it a number instead.
    def __init__(self, measuring: Callable[[AgentServer], float] | None = None) -> None:
        self._measuring = measuring or livekits_own_measure()
        self._refused = False

    # livekit calls this every 2.5s (worker.py:810) and again before every availability check
    # (worker.py:1313-1321), so the crossing is seen on livekit's own tick: nothing here polls.
    def __call__(self, server: AgentServer) -> float:
        """The machine's load, as livekit measures it, with a line whenever the answer changes."""
        load = self._measuring(server)
        refused = load >= REFUSED_AT
        if refused != self._refused:
            self._refused = refused
            self._say_what_changed(load, refused)
        return load

    # Once per crossing, never per tick: what an operator needs is the moment the box went
    # invisible to the dispatcher and the moment it came back, each with the number that decided.
    def _say_what_changed(self, load: float, refused: bool) -> None:
        """Why this worker is, or is no longer, invisible to livekit's dispatcher."""
        if refused:
            logger.warning(
                "machine load %.2f is at or over %.2f: livekit will route no job to this worker "
                "until it falls",
                load,
                REFUSED_AT,
            )
        else:
            logger.info(
                "machine load %.2f is under %.2f: livekit is routing jobs here again",
                load,
                REFUSED_AT,
            )
