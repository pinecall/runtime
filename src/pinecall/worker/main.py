"""The fleet process: one AgentServer, one rtc_session, and the Worker every job process builds."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import TypedDict

from livekit.agents import AgentServer, JobContext, JobProcess

from pinecall._settings import Settings, load_settings, variable_of
from pinecall.evals.score import JudgedWhen
from pinecall.providers.session_vendors import warm_the_vendor_tables
from pinecall.session.voice import a_bridge
from pinecall.session.voice.kit import kit_for
from pinecall.worker import recordings
from pinecall.worker.client import reaching
from pinecall.worker.entry import Worker, answer
from pinecall.worker.load import MachineLoad, SlotLoad, reports_no_load
from pinecall.worker.telemetry import traced_to

# What livekit needs to register a worker at all: the media plane, and the pair that signs.
LIVEKIT_FIELDS: tuple[str, ...] = ("livekit_url", "livekit_api_key", "livekit_api_secret")

# How long a stop waits for the calls this worker holds to end by themselves. livekit's own default
# is an HOUR, and the unit's TimeoutStopSec is fifteen minutes, so the drain could never finish and
# systemd's SIGKILL was the only thing that ever ended it — every call it still held dying with no
# call.ended. Ten minutes leaves five of the unit's fifteen for what follows: past it livekit shuts
# each remaining job down, and a job that is shut down seals its own log as `drained`.
DRAIN_S = 10 * 60

# And what ONE job gets, once it is told to shut down, to run its shutdown callbacks. Ours is the
# seal (worker/entry.py `sealing`): call.ended, the hang-up's one memory extraction (the `remember`
# budget, 8 s), call.summary, the judges, call.score. livekit's default of ten seconds cuts that in
# half and kills the process mid-seal, which is a log with a call.ended and no end. Sixty seconds
# covers it, and DRAIN_S + this still sits inside the unit's TimeoutStopSec.
SEALING_S = 60.0

log = logging.getLogger(__name__)


# livekit runs a call in a process of its own and hands that process the entrypoint BY NAME: the
# function is pickled as module plus qualname (spawn on macOS, forkserver on Linux, worker.py:259),
# so it has to be importable, and nothing it closed over in the parent travels with it. A closure
# here broke on the first dispatched job of ms-3 — "Can't get local object" — which is why the
# Worker is built in the job's own process, from the environment that process inherited.
async def job(ctx: JobContext) -> None:
    """The entrypoint of every job: this process's Worker, then the call is answered."""
    await answer(ctx, a_worker(load_settings()))


# One key, issued by a person, wherever this worker runs. It used to be three: the org key, a dev
# key exported by hand, and — winning over both — whatever a local gateway had left in
# ~/.pinecall/dev, because a gateway on a dev key honoured that key and no other. The order was
# invisible and got it wrong both ways: PINECALL_WORKER_KEY in the shell against a gateway on a dev
# key killed every job of a spoken suite on `GET /v1/routes: 401` until the run timed out
# (2026-09-11). There is one runtime now and one key: `keys issue --scope app`.
def the_key_for(settings: Settings) -> str:
    """What this worker knocks at its gateway with. Empty is a worker nobody issued a key for."""
    return settings.worker_key or ""


def a_worker(settings: Settings) -> Worker:
    """What every job of a process shares: the gateway, the vendors, the bridge, the recordings."""
    gateway = reaching(settings.gateway_url, the_key_for(settings))
    return Worker(
        gateway=gateway,
        kit=kit_for(settings),
        timezone=settings.timezone,
        # The worker is who hands a spoken call its judge: the session judges nothing itself. And
        # its memory: a job process has no database, so the gateway is the session's Lookup and
        # Rememberer too — the same object, three protocols — under the budgets the box set. The
        # judge asks the gateway first whether the call's org judges its calls at all.
        bridging=partial(
            a_bridge,
            score=JudgedWhen(gateway.judging),
            lookup=gateway,
            rememberer=gateway,
            budgets=settings.budgets,
        ),
        keeping=recordings.keeping_for(settings),
        default_agent=settings.agent,
        app=settings.app,
    )


# livekit's own default when the instance names no count is left to livekit, so it is not spelled
# twice: the keyword is passed only when there is one to pass.
class Warm(TypedDict, total=False):
    """The one AgentServer keyword an instance may or may not set: how many processes stay warm."""

    num_idle_processes: int


# The two models a call cannot wait for are livekit's own and livekit preloads them itself
# (worker.py:747-759). Ours are the vendor plugin packages, and those it cannot know about: the
# tables are read lazily on the first pipeline, which measured 1.3 s to 4.2 s INSIDE the job with
# the caller already in the room. `setup_fnc` runs in an idle process before a job is assigned to
# it, which is where that second belongs — see docs/decisions/worker.md.
#
# The load is HANDED to livekit too, and which one is the difference between three kinds of
# process: livekit-server routes no job to a worker reporting 0.7 or more. A worker with a
# measured `max_jobs` reports its slots, which is what a box of its own and a fleet are counted
# in; one without reports the machine's CPU average, right for a box it shares with the SFU; and
# `dev` reports none, which is what dev mode already means (load.py).
#
# The url and the key pair are HANDED to livekit, never left to it: AgentServer falls back to
# os.environ for all three (worker.py:333-335) and dies at worker.py:680 when they are unset, which
# is what `worker dev` did beside a perfectly good runtime/.env — a file is not the environment.
# Settings is the only reader of either, and livekit gets the values by its own parameters.
# The name is the instance's fleet (`PINECALL_FLEET`): its gateway writes the same word into every
# room config it mints, and Settings holds it to a slug's alphabet and never empty
# (types/dispatch.py says why). The warm processes are livekit's own count unless the instance
# says one.
def a_server(settings: Settings, *, gated_by_machine_load: bool = True) -> AgentServer:
    """The process: the one entrypoint that answers a job, under the fleet name it joins by."""
    warm = (
        Warm()
        if settings.idle_processes is None
        else Warm(num_idle_processes=settings.idle_processes)
    )
    server = AgentServer(
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        load_fnc=_the_gate(settings, gated_by_machine_load),
        setup_fnc=warmed,
        # The two clocks a stop runs on, both of them livekit's and both of them wrong for a box
        # by default: how long the drain waits, and how long one job's seal may take.
        drain_timeout=DRAIN_S,
        shutdown_process_timeout=SEALING_S,
        # Loopback and a port of its own: livekit's default is 8081, which is TEI's, and a full box
        # runs both. Nothing outside the machine reads this server, so it never leaves loopback.
        host="127.0.0.1",
        port=settings.worker_http_port,
        **warm,
    )
    # livekit refuses a second one itself (worker.py:502); the fleet name is ours to insist on.
    server.rtc_session(job, agent_name=settings.fleet)
    return server


def _the_gate(
    settings: Settings, gated: bool
) -> Callable[[AgentServer], float] | Callable[[], float]:
    """Slots when this worker was measured, the machine's CPU when it was not, nothing for dev."""
    if not gated:
        return reports_no_load
    if settings.max_jobs is not None:
        return SlotLoad(settings.max_jobs)
    return MachineLoad()


# livekit hands the hook the process it is warming; nothing of ours needs it, and the vendors it
# imports are held by the modules themselves, so nothing is written into `proc.userdata`.
#
# `talk` reaches this from livekit's `job_thread_runner`, not from a process of its own, and a
# plugin registers itself on import (agents/plugin.py:31-33). The console's tables were therefore
# already read on the main thread before livekit was handed the process (cli/worker.py
# THREADED_VERBS), and reading them is idempotent, so this call is a no-op there.
def warmed(proc: JobProcess) -> None:  # noqa: ARG001 — livekit hands every setup the process
    """Everything a call would otherwise wait for, done while the process is still idle."""
    warm_the_vendor_tables()
    traced_to(load_settings())


# Which of the three a box has not set, by the variable name an operator would type. livekit
# registers a worker with all three (worker.py:679-690); `talk` runs unregistered and needs none.
def unset_livekit_variables(settings: Settings) -> list[str]:
    """The LiveKit variables this Settings found nowhere — empty when the worker can register."""
    return [variable_of(field) for field in LIVEKIT_FIELDS if not getattr(settings, field)]
