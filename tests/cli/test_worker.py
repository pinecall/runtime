"""The worker group: our four verbs, livekit's own flags behind them, and the recording path."""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from livekit.agents import AgentServer

from pinecall._settings import load_settings
from pinecall.cli import build_parser, worker
from pinecall.worker import recording_paths
from pinecall.worker.heartbeat import CORDONED_EXIT, Heartbeats
from pinecall.worker.load import MachineLoad, reports_no_load

pytestmark = pytest.mark.unit

_THE_THREE = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


def test_every_verb_names_the_livekit_verb_it_hands_the_process_to() -> None:
    assert worker.LIVEKIT_VERBS == {
        "dev": "dev",
        "start": "start",
        "overflow": "start",
        "talk": "console",
        "download-files": "download-files",
    }


def test_the_verb_and_livekits_own_flags_arrive_apart() -> None:
    arguments = build_parser().parse_args(["worker", "start", "--drain-timeout", "30"])
    assert (arguments.verb, arguments.flags) == ("start", ["--drain-timeout", "30"])


def test_the_gateway_and_the_agent_are_settings_not_flags_because_a_job_is_another_process() -> (
    None
):
    """A flag parsed here never reaches the job process livekit spawns; PINECALL_AGENT does."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["worker", "--agent", "clinica-norte", "talk"])


def test_the_group_with_no_verb_prints_its_verbs(capsys: pytest.CaptureFixture[str]) -> None:
    assert worker.run(build_parser().parse_args(["worker"])) == 0
    printed = capsys.readouterr().out
    assert all(verb in printed for verb in worker.VERBS)


def test_the_process_is_handed_to_livekit_with_the_argv_an_agent_script_would_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """livekit's CLI reads the process's own argv; ours is written rather than typed."""
    seen: list[list[str]] = []
    monkeypatch.setattr(worker, "run_app", _remembering(seen))
    worker.hand_over("talk", ["--record"], load_settings())
    assert seen == [["pinecall-runtime worker talk", "console", "--record"]]


# Which verb reports which load, at the seam where the process is handed over: `dev` on a busy
# laptop was registering and never being dispatched to (worker/main.py).
def test_the_dev_process_is_handed_over_reporting_no_load(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _the_server_handed_over(monkeypatch, "dev").load_fnc is reports_no_load


def test_the_box_process_is_handed_over_with_livekits_own_cpu_average(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(_the_server_handed_over(monkeypatch, "start").load_fnc, MachineLoad)


def _the_server_handed_over(monkeypatch: pytest.MonkeyPatch, verb: str) -> AgentServer:
    """The AgentServer livekit's CLI would have run, caught on its way there."""
    servers: list[AgentServer] = []
    monkeypatch.setattr(worker, "run_app", servers.append)
    worker.hand_over(verb, [], load_settings())
    return servers[0]


def test_asking_to_record_prints_the_path_before_the_microphone_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PINECALL_RECORDINGS", str(tmp_path))
    monkeypatch.setattr(worker, "hand_over", _handed_over)
    assert worker.run(build_parser().parse_args(["worker", "talk", "--record"])) == 0
    printed = capsys.readouterr().out
    assert str(tmp_path) in printed and recording_paths.AUDIO_FILE in printed


# The failure this card was cut for: `worker dev` died on livekit's own sentence about the
# environment, beside a runtime/.env that had all three. Ours names the file instead.
def test_a_worker_that_cannot_reach_livekit_says_so_naming_runtime_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(worker, "hand_over", _never_handed_over)
    _with_no_livekit_anywhere(tmp_path, monkeypatch)
    assert worker.run(build_parser().parse_args(["worker", "dev"])) == 2
    refusal = capsys.readouterr().err
    assert "runtime/.env" in refusal
    assert all(variable in refusal for variable in _THE_THREE)
    assert "environment variable" not in refusal


def test_the_console_still_runs_on_a_box_that_has_no_livekit_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`talk` registers with nobody (worker.py:678), so it is never refused for a missing key."""
    seen: list[list[str]] = []
    monkeypatch.setattr(worker, "run_app", _remembering(seen))
    _with_no_livekit_anywhere(tmp_path, monkeypatch)
    assert worker.run(build_parser().parse_args(["worker", "talk"])) == 0
    assert seen == [["pinecall-runtime worker talk", "console"]]


# The console has no job process of its own: livekit runs its job in a thread of THIS one, and a
# vendor plugin registers itself on import, which livekit refuses off the main thread. This drives
# the console's whole setup path — hand_over, then `warmed` from a thread named as livekit names
# it — in a FRESH interpreter, because the pin is about which thread did the importing and the
# suite's own session fixture has already done it here. Without the warm in hand_over it raises
# RuntimeError("Plugins must be registered on the main thread"), which is the bug this card fixed.
_THE_CONSOLE_SETUP_PATH = """
import threading
from unittest.mock import patch
from pinecall._settings import load_settings
from pinecall.cli import worker
from pinecall.worker.main import warmed

def livekit_runs_the_job_in_a_thread(server):
    raised = []
    def job_thread_runner():
        try:
            warmed(None)
        except BaseException as error:
            raised.append(error)
    thread = threading.Thread(target=job_thread_runner, name="job_thread_runner")
    thread.start()
    thread.join()
    if raised:
        raise raised[0]

with patch.object(worker, "run_app", livekit_runs_the_job_in_a_thread):
    worker.hand_over("talk", [], load_settings())
"""


def test_the_console_setup_runs_off_the_main_thread_without_refusing_to_register_a_plugin(
    tmp_path: Path,
) -> None:
    run = subprocess.run(
        [sys.executable, "-c", _THE_CONSOLE_SETUP_PATH],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr


def test_the_console_reads_the_vendor_tables_before_livekit_is_handed_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`talk`'s job runs in a thread of ours, so the importing happens while the process is."""
    assert _the_verbs_that_warmed(monkeypatch) == ["talk"]


def _with_no_livekit_anywhere(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A box with none of the three in its environment and no .env beside it to fall back on."""
    monkeypatch.chdir(directory)
    for variable in _THE_THREE:
        monkeypatch.setenv(variable, "")


def _remembering(seen: list[list[str]]) -> Callable[[object], None]:
    """livekit's CLI, replaced by the argv it would have read."""

    def run_app(_server: object) -> None:
        seen.append(list(worker.sys.argv))

    return run_app


def _handed_over(_verb: str, _flags: list[str], _settings: object) -> int:
    """The process reaching livekit, with nothing behind it: this file never opens a microphone."""
    return 0


def _never_handed_over(_verb: str, _flags: list[str], _settings: object) -> int:
    """A refused recording must not reach livekit, and this is the assertion that it does not."""
    raise AssertionError("the process was handed over after the recording was refused")


def _handed_over_to_nobody(_server: object) -> None:
    """livekit's CLI, replaced by nothing at all: this helper is about what ran before it."""


def _the_verbs_that_warmed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Which of the four verbs read the vendor tables on the CLI's own main thread."""
    warmed_by: list[str] = []
    monkeypatch.setattr(worker, "run_app", _handed_over_to_nobody)
    for verb in worker.VERBS:
        monkeypatch.setattr(
            worker, "warm_the_vendor_tables", lambda verb=verb: warmed_by.append(verb)
        )
        worker.hand_over(verb, [], load_settings())
    return warmed_by


# click ends livekit's CLI with SystemExit(0), which swallowed a cordon's exit 3 the first time a
# worker was cordoned on a real box: systemd read 0, restarted it, and it cordoned itself again.
def test_a_cordoned_worker_leaves_with_exit_3_through_clicks_own_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def clicks_exit(_server: object) -> None:
        raise SystemExit(0)

    monkeypatch.setattr(worker, "run_app", clicks_exit)
    monkeypatch.setattr(worker.Heartbeats, "start_with", _cordoned_at_once)
    assert worker.hand_over("start", [], load_settings()) == CORDONED_EXIT


def test_an_uncordoned_worker_leaves_with_clicks_own_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def clicks_exit(_server: object) -> None:
        raise SystemExit(0)

    monkeypatch.setattr(worker, "run_app", clicks_exit)
    monkeypatch.setattr(worker.Heartbeats, "start_with", _never_beating)
    assert worker.hand_over("start", [], load_settings()) == 0


def _cordoned_at_once(pulse: Heartbeats) -> None:
    """The hub's cordon, arrived before the first beat: what a worker started cordoned would see."""
    pulse.cordoned = True


def _never_beating(_pulse: Heartbeats) -> None:
    """A heartbeat that never starts: this file opens no socket."""
