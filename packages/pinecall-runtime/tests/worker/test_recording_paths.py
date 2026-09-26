"""Where a call's audio is kept, and which of the two recorders is the one that writes it."""

from datetime import datetime
from pathlib import Path

import pytest
from livekit.agents.cli import _legacy

from pinecall.settings import Settings, load_settings
from pinecall.worker import recording_paths
from tests.worker.fakes import a_job_in

pytestmark = pytest.mark.unit


def test_a_call_gets_its_own_directory_under_the_root_the_box_named(tmp_path: Path) -> None:
    directory = recording_paths.destination_for("CA_7", _keeping(tmp_path))
    assert directory == tmp_path / "CA_7"
    assert directory.is_dir()


# The recorder is a container: a member of the group that owns the recordings and the owner of
# nothing. A directory made with the umask's own mode gave it r-x and no w, and every recording
# died with `permission denied` after the call had already been held.
def test_the_directory_a_call_gets_is_writable_by_the_group_that_owns_it(tmp_path: Path) -> None:
    directory = recording_paths.destination_for("CA_7", _keeping(tmp_path))
    assert directory.stat().st_mode & 0o7770 == 0o2770


def test_a_directory_the_box_will_not_mark_setgid_is_said_and_still_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A fence forbidding the bit lost every recording of 2026-09-26 without a line."""

    def refused(self: Path, mode: int) -> None:  # noqa: ARG001 — Path.chmod's shape
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "chmod", refused)
    directory = recording_paths.destination_for("CA_7", _keeping(tmp_path))
    assert directory == tmp_path / "CA_7"
    assert "not group-writable" in caplog.text
    assert "Operation not permitted" in caplog.text


def test_the_directory_is_ours_and_the_file_inside_it_is_livekits(tmp_path: Path) -> None:
    """RecorderIO writes `audio.ogg` into the session's directory (agent_session.py:1043)."""
    directory = recording_paths.destination_for("CA_7", _keeping(tmp_path))
    assert recording_paths.audio_in(directory) == tmp_path / "CA_7" / "audio.ogg"


def test_two_console_sessions_of_the_same_room_are_filed_apart() -> None:
    """Every console session is `console-room`, so the moment is what tells two of them apart."""
    morning = recording_paths.console_session_name(datetime(2026, 9, 7, 9, 30, 0))
    evening = recording_paths.console_session_name(datetime(2026, 9, 7, 21, 5, 12))
    assert (morning, evening) == ("console-20260907-093000", "console-20260907-210512")


def test_the_console_recorder_is_pointed_at_the_directory_we_composed(tmp_path: Path) -> None:
    """The job under a console reads the console's directory, not a temporary one (job.py:236)."""
    audio = recording_paths.kept_by_the_console(tmp_path / "console-20260907-093000")
    console = _legacy.AgentsConsole.get_instance()
    assert console.session_directory == tmp_path / "console-20260907-093000"
    assert audio == console.session_directory / recording_paths.AUDIO_FILE


def test_on_a_box_the_pointer_is_our_file_and_the_session_records_nothing(tmp_path: Path) -> None:
    """The box's egress writes it (worker/recorder.py), so livekit's own recorder is never asked."""
    job = a_job_in(tmp_path / "livekit-tmp")
    audio = recording_paths.kept_by_the_job(job, tmp_path / "CA_7")
    assert audio == tmp_path / "CA_7" / recording_paths.AUDIO_FILE
    assert recording_paths.records_itself() is False
    assert recording_paths.asked_of_the_session(audio) is False


def test_under_a_console_the_pointer_is_the_consoles_file_and_only_when_it_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The console's directory is already ours; livekit writes the file only under --record."""
    console = _legacy.AgentsConsole.get_instance()
    monkeypatch.setattr(console, "enabled", True)
    monkeypatch.setattr(console, "record", False)
    job = a_job_in(tmp_path / "console-20260907-093000")
    assert recording_paths.kept_by_the_job(job, tmp_path / "console-room") is None
    monkeypatch.setattr(console, "record", True)
    audio = recording_paths.kept_by_the_job(job, tmp_path / "console-room")
    assert audio == tmp_path / "console-20260907-093000" / recording_paths.AUDIO_FILE
    assert job.session_directory == tmp_path / "console-20260907-093000"
    # And the console is the one place the session is asked to record itself: there is no room on
    # any server for an egress to compose.
    assert recording_paths.asked_of_the_session(audio) == recording_paths.AUDIO_ONLY


def test_what_a_call_records_is_the_audio_and_nothing_that_leaves_the_box() -> None:
    """A RecordingOptions key left out defaults to on (agent_session.py:104): all four are said."""
    assert recording_paths.AUDIO_ONLY == {
        "audio": True,
        "traces": False,
        "logs": False,
        "transcript": False,
    }


def test_the_process_holds_the_root_and_a_call_asks_with_its_id(tmp_path: Path) -> None:
    assert recording_paths.keeping_for(_keeping(tmp_path))("CA_7") == tmp_path / "CA_7"


def _keeping(root: Path) -> Settings:
    """A box that keeps its audio under this directory."""
    return load_settings().model_copy(update={"recordings_root": str(root)})
