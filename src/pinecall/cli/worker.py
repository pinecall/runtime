"""`pinecall-runtime worker`: the fleet process, and the console that puts a microphone on it."""

import argparse
import sys

from livekit.agents.cli import run_app

from pinecall._settings import Settings, load_settings
from pinecall.providers.pipeline import warm_the_vendor_tables
from pinecall.worker import recordings
from pinecall.worker.main import a_server, unset_livekit_variables

PURPOSE: str = "the fleet: dev | start | talk | download-files"

# Our verb, and the verb of livekit's own CLI it hands the process to. Everything a worker does —
# registering, draining, the microphone, the speaker, the recorder, the per-turn latency line —
# is livekit's; this group is the name we give it and the Worker we hand it.
LIVEKIT_VERBS: dict[str, str] = {
    "dev": "dev",
    "start": "start",
    "talk": "console",
    "download-files": "download-files",
}
VERBS: tuple[str, ...] = tuple(LIVEKIT_VERBS)

# livekit's console flag for keeping the audio. `pinecall talk --record` arrives as this.
RECORD_FLAG = "--record"

# What a box that keeps no audio answers when somebody asks for a recording anyway.
REFUSED = f"RECORD=0: this box keeps no audio, so {RECORD_FLAG} cannot run"

# The two verbs that REGISTER with livekit, and so need the url and the key pair. `talk` runs the
# session unregistered (worker.py:678) and `download-files` never opens a socket: neither needs one.
REGISTERING_VERBS: tuple[str, ...] = ("dev", "start")

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
        if destination is None:
            print(REFUSED, file=sys.stderr)
            return 2
        print(f"recording to {recordings.kept_by_the_console(destination)}")
    hand_over(arguments.verb, flags, settings)
    return 0


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
def hand_over(verb: str, flags: list[str], settings: Settings) -> None:
    """Run livekit's CLI over our AgentServer, with the verb and flags this group was given."""
    # This is the last line that is certainly on the main thread: `dev` and `start` pay for the
    # vendor imports in the idle job PROCESS, where nobody is listening, but the console's job has
    # no process of its own and its setup hook would raise (THREADED_VERBS).
    if verb in THREADED_VERBS:
        warm_the_vendor_tables()
    sys.argv = [f"pinecall-runtime worker {verb}", LIVEKIT_VERBS[verb], *flags]
    run_app(a_server(settings, gated_by_machine_load=verb in GATED_BY_MACHINE_LOAD))
