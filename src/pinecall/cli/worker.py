"""`pinecall-runtime worker`: the fleet process, and the console that puts a microphone on it."""

import argparse
import socket
import sys

from livekit.agents import AgentServer
from livekit.agents.cli import run_app

from pinecall._settings import Settings, load_settings
from pinecall.providers.pipeline import warm_the_vendor_tables
from pinecall.worker import overflow, recordings
from pinecall.worker.client import reaching
from pinecall.worker.heartbeat import Heartbeats
from pinecall.worker.main import a_server, the_key_for, unset_livekit_variables

PURPOSE: str = "the fleet: dev | start | overflow | talk | download-files"

# Our verb, and the verb of livekit's own CLI it hands the process to. Everything a worker does —
# registering, draining, the microphone, the speaker, the recorder, the per-turn latency line —
# is livekit's; this group is the name we give it and the Worker we hand it.
LIVEKIT_VERBS: dict[str, str] = {
    "dev": "dev",
    "start": "start",
    "overflow": "start",
    "talk": "console",
    "download-files": "download-files",
}
VERBS: tuple[str, ...] = tuple(LIVEKIT_VERBS)

# livekit's console flag for keeping the audio. `pinecall talk --record` arrives as this.
RECORD_FLAG = "--record"

# The two verbs that REGISTER with livekit, and so need the url and the key pair. `talk` runs the
# session unregistered (worker.py:678) and `download-files` never opens a socket: neither needs one.
REGISTERING_VERBS: tuple[str, ...] = ("dev", "start", "overflow")

# The verb that is NOT a seat: the overflow agent on the hub answers only when every real worker is
# full, says one sentence, takes a number and hangs up (worker/overflow.py). It has its own server.
THE_OVERFLOW = "overflow"

# The verb whose job livekit runs in a THREAD of THIS process rather than in one of its own:
# `console` sets JobExecutorType.THREAD on the server it is given (cli/cli.py:227, and the legacy
# console at cli/_legacy.py:1491), so the setup hook is called from `job_thread_runner`
# (ipc/job_proc_lazy_main.py:478 → ipc/proc_client.py:46 → :225). A vendor plugin package registers
# itself as it is imported and livekit refuses that anywhere but the main thread
# (agents/plugin.py:31-33), so `talk` reads the vendor tables HERE, before livekit takes the
# process over — see hand_over.
THREADED_VERBS: tuple[str, ...] = ("talk",)

# The verb whose worker keeps livekit's CPU-average load report, which is the backpressure a box
# wants. `dev` reports none: dev mode already refuses no job on load, and the load a worker reports
# is what livekit-server gates the dispatch on — see worker/main.py.
GATED_BY_MACHINE_LOAD: tuple[str, ...] = ("start",)


# Where the gateway is and which agent a job that names none is for are PINECALL_GATEWAY_URL and
# PINECALL_AGENT, not flags: a job runs in a process livekit spawns, and the environment is what
# that process inherits (worker/main.py).
def configure(parser: argparse.ArgumentParser) -> None:
    """The verb, and whatever else livekit's own CLI reads."""
    parser.add_argument("verb", nargs="?", choices=VERBS, help=" | ".join(VERBS))
    parser.add_argument(
        "flags", nargs=argparse.REMAINDER, help="passed through to livekit's own CLI"
    )
    parser.set_defaults(run=run)


def run(arguments: argparse.Namespace) -> int:
    """Build the one Worker this process serves, and hand the process to livekit's CLI."""
    if arguments.verb is None:
        print(f"usage: pinecall-runtime worker <verb>: {' | '.join(VERBS)}")
        return 0
    settings = load_settings()
    if arguments.verb in REGISTERING_VERBS:
        missing = unset_livekit_variables(settings)
        if missing:
            print(unreachable(missing), file=sys.stderr)
            return 2
    flags: list[str] = list(arguments.flags)
    if RECORD_FLAG in flags:
        # Decided before the microphone is opened, so a `pinecall talk` of tomorrow does not
        # write over the one from today and the path is in the terminal from its first line.
        destination = recordings.destination_for(recordings.a_console_session(), settings)
        print(f"recording to {recordings.kept_by_the_console(destination)}")
    return hand_over(arguments.verb, flags, settings)


# livekit's own sentence for the same three ("ws_url is required, or set LIVEKIT_URL environment
# variable", worker.py:680) sends the reader to the environment, which is exactly where the values
# were NOT: they belong in the file Settings reads, and this says so by name.
def unreachable(missing: list[str]) -> str:
    """Why this worker cannot register, naming the variables and the file they are written in."""
    return (
        f"{', '.join(missing)}: a worker reaches livekit by url and key pair. "
        "Write them in runtime/.env (runtime/.env.example has the line) "
        "and `pinecall-runtime doctor` will read the file back to you."
    )


# livekit's CLI reads the process's own argv, which is how every agent script in the world starts
# it. Ours is a group of a larger CLI, so the argv it reads is written here rather than typed.
#
# A registering worker beats to the hub beside livekit's own socket (worker/heartbeat.py): the
# fleet is counted there, and a cordon comes back the same way. The exit code says why the
# process left — a cordon is on purpose, and the unit keeps it down.
def hand_over(verb: str, flags: list[str], settings: Settings) -> int:
    """Run livekit's CLI over our AgentServer, with the verb and flags this group was given."""
    # This is the last line that is certainly on the main thread: `dev` and `start` pay for the
    # vendor imports in the idle job PROCESS, where nobody is listening, but the console's job has
    # no process of its own and its setup hook would raise (THREADED_VERBS).
    if verb in THREADED_VERBS:
        warm_the_vendor_tables()
    sys.argv = [f"pinecall-runtime worker {verb}", LIVEKIT_VERBS[verb], *flags]
    gateway = reaching(settings.gateway_url, the_key_for(settings))
    if verb == THE_OVERFLOW:
        return _ran(overflow.a_server(settings, gateway))
    server = a_server(settings, gated_by_machine_load=verb in GATED_BY_MACHINE_LOAD)
    pulse = None
    if verb in REGISTERING_VERBS:
        pulse = Heartbeats(server, gateway, worker_name(settings), settings.max_jobs)
        pulse.start_with()
    code = _ran(server)
    return pulse.exit_code() if pulse is not None and pulse.cordoned else code


# livekit's CLI is a click group run standalone, and click ends the process itself: run_app never
# returns, it raises SystemExit(0) — which is how a cordon's exit 3 came out as a 0 and systemd
# restarted the worker into a cordon again (2026-09-11). The exit is caught here so this function
# has a return value, and the cordon's code wins over click's.
def _ran(server: AgentServer) -> int:
    """livekit's CLI over the server, and the code it would have left with."""
    try:
        run_app(server)
    except SystemExit as left:
        return left.code if isinstance(left.code, int) else 1
    return 0


def worker_name(settings: Settings) -> str:
    """What this worker calls itself to the hub: PINECALL_WORKER_NAME, else the short hostname."""
    return settings.worker_name or socket.gethostname().split(".")[0]
