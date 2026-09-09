"""The fleet process: one AgentServer, one rtc_session, and the Worker every job process builds."""

from __future__ import annotations

from functools import partial

from livekit.agents import AgentServer, JobContext, JobProcess

from pinecall._settings import Settings, load_settings, variable_of
from pinecall.evals import a_score
from pinecall.providers.pipeline import warm_the_vendor_tables
from pinecall.session.voice import a_bridge
from pinecall.session.voice.kit import kit_for
from pinecall.types.dispatch import WORKER_NAME
from pinecall.worker import recordings
from pinecall.worker.client import reaching
from pinecall.worker.entry import Worker, answer
from pinecall.worker.load import MachineLoad, reports_no_load

# What livekit needs to register a worker at all: the media plane, and the pair that signs.
LIVEKIT_FIELDS: tuple[str, ...] = ("livekit_url", "livekit_api_key", "livekit_api_secret")


# livekit runs a call in a process of its own and hands that process the entrypoint BY NAME: the
# function is pickled as module plus qualname (spawn on macOS, forkserver on Linux, worker.py:259),
# so it has to be importable, and nothing it closed over in the parent travels with it. A closure
# here broke on the first dispatched job of ms-3 — "Can't get local object" — which is why the
# Worker is built in the job's own process, from the environment that process inherited.
async def job(ctx: JobContext) -> None:
    """The entrypoint of every job: this process's Worker, then the call is answered."""
    await answer(ctx, a_worker(load_settings()))


# The api key before the dev key, and the order is not a preference. A box has PINECALL_API_KEY —
# a real row in api_keys, issued by `pinecall-runtime keys issue` — and never a dev key, because a
# gateway that reads one opens no database at all. A laptop has the dev key and no api key. A
# laptop that has BOTH is pointed at a gateway with no tables, where only one of the two can work:
# the api key is the deliberate one, and a worker that preferred a leftover dev key would knock as
# a fleet nobody asked for. docs/decisions/keys.md.
def a_worker(settings: Settings) -> Worker:
    """What every job of a process shares: the gateway, the vendors, the bridge, the recordings."""
    return Worker(
        gateway=reaching(settings.gateway_url, settings.api_key or settings.dev_key or ""),
        kit=kit_for(settings),
        # The worker is who hands a spoken call its judge: the session judges nothing itself.
        bridging=partial(a_bridge, score=a_score),
        keeping=recordings.keeping_for(settings),
        default_agent=settings.agent,
        app=settings.app,
    )


# The two models a call cannot wait for are livekit's own and livekit preloads them itself
# (worker.py:747-759). Ours are the vendor plugin packages, and those it cannot know about: the
# tables are read lazily on the first pipeline, which measured 1.3 s to 4.2 s INSIDE the job with
# the caller already in the room. `setup_fnc` runs in an idle process before a job is assigned to
# it, which is where that second belongs — see docs/decisions/worker.md.
#
# The load is HANDED to livekit too, and which one is the difference between a box and a laptop:
# livekit-server routes no job to a worker reporting 0.7 or more, and the machine's CPU average is
# what a worker reports unless it is given a load_fnc (load.py). A box keeps it and says when it
# crosses; `dev` reports none, which is what dev mode already means.
#
# The url and the key pair are HANDED to livekit, never left to it: AgentServer falls back to
# os.environ for all three (worker.py:333-335) and dies at worker.py:680 when they are unset, which
# is what `worker dev` did beside a perfectly good runtime/.env — a file is not the environment.
# Settings is the only reader of either, and livekit gets the values by its own parameters.
# The name is the dispatch's (types/dispatch.py): the token door writes the same word into every
# room config it mints. An empty agent_name means implicit dispatch to every room in the deployment
# (worker.py:219) — somebody else's call, answered by us — so the name is never empty and this
# module refuses one.
def a_server(
    settings: Settings, fleet: str = WORKER_NAME, *, gated_by_machine_load: bool = True
) -> AgentServer:
    """The process: the one entrypoint that answers a job, under the fleet name it joins by."""
    if not fleet:
        raise ValueError("a worker joins a fleet by name: an empty agent_name answers every room")
    server = AgentServer(
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        load_fnc=MachineLoad() if gated_by_machine_load else reports_no_load,
        setup_fnc=warmed,
    )
    # livekit refuses a second one itself (worker.py:502); the fleet name is ours to insist on.
    server.rtc_session(job, agent_name=fleet)
    return server


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


# Which of the three a box has not set, by the variable name an operator would type. livekit
# registers a worker with all three (worker.py:679-690); `talk` runs unregistered and needs none.
def unset_livekit_variables(settings: Settings) -> list[str]:
    """The LiveKit variables this Settings found nowhere — empty when the worker can register."""
    return [variable_of(field) for field in LIVEKIT_FIELDS if not getattr(settings, field)]
