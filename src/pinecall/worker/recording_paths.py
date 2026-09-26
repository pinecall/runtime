"""Where a call's audio is kept: the one place a recording's path is composed."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from functools import partial
from pathlib import Path

from livekit.agents import JobContext
from livekit.agents.cli import _legacy
from livekit.agents.voice import RecordingOptions

from pinecall._settings import Settings

# livekit writes the recording as `audio.ogg` inside the session's directory
# (voice/agent_session.py:1043). The file name is the library's; the directory is ours.
AUDIO_FILE = "audio.ogg"

# What a call that keeps audio asks the session to record: the audio, and only the audio. A key
# left out of RecordingOptions defaults to on (agent_session.py:104), and the other three are
# uploads to an observability host — nothing of a call leaves the box for a file the log points at.
# Asked of the session under the CONSOLE alone: see kept_by_the_job below.
AUDIO_ONLY: RecordingOptions = {"audio": True, "traces": False, "logs": False, "transcript": False}

type Keeping = Callable[[str], Path]
"""What one process holds: a call id in, the directory its audio goes in out."""


def keeping_for(settings: Settings) -> Keeping:
    """The process's way to a call's directory: the root read once at startup."""
    return partial(destination_for, settings=settings)


# WHETHER a call is recorded is not asked here and never was the box's to answer any more: it is
# the agent's own setting, resolved with the rest of its world (types/tuning.py), and the worker
# asks for this directory only for a call that keeps its audio.
def destination_for(call: str, settings: Settings) -> Path:
    """The directory this call's audio goes in, created for whoever is going to write in it."""
    directory = Path(settings.recordings_root) / call
    directory.mkdir(parents=True, exist_ok=True)
    # Group-writable, and setgid so the file belongs to the group and not to whoever wrote it.
    # `mkdir` masks its mode with the umask (022 here), which gave the group r-x and no w — and
    # the recorder is a member of that group and the owner of nothing, so every recording ended
    # `Local upload failed: … permission denied` and the call's summary pointed at no audio
    # (2026-09-21, the first recorded call on the box). It is said here because this is the one
    # place a recording's directory is made; the ROOT's mode is the box's (infra/box/tmpfiles.d).
    with suppress(OSError):
        directory.chmod(0o2770)
    return directory


def audio_in(directory: Path) -> Path:
    """The pointer the log carries: the file livekit will write inside that directory."""
    return directory / AUDIO_FILE


# The console is always the same room, so the room's name cannot tell two sessions apart and a
# second `pinecall talk` would record over the first. The moment can.
def console_session_name(now: datetime | None = None) -> str:
    """The name a console session is filed under, since every one of them is `console-room`."""
    return f"console-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}"


# The console owns the microphone, and a job under it reads the console's directory rather than
# the temporary one every other job gets (job.py:236-240). livekit exposes that directory
# read-only (cli/_legacy.py:440) and names it after the clock, so redirecting it is what keeps a
# recording's path decided in one place.
def kept_by_the_console(destination: Path) -> Path:
    """Point livekit's console recorder at our directory, and answer with the file it will write."""
    console = _legacy.AgentsConsole.get_instance()
    console._session_directory = destination  # pyright: ignore[reportPrivateUsage]
    return audio_in(destination)


# Two writers, and which one it is depends on where the job runs. On a box the recorder is the
# BOX's — one room composite egress per call, which writes the file itself (worker/recorder.py) —
# and the session records nothing, because the session only ever knew two sources: the participant
# it was pinned to and its own voice. Everything else the call heard, the hold melody and a
# supervisor who took the line, was published as a track of its own and was never in the file.
#
# Under livekit's console (`pinecall talk`) there is no room on any server to compose, so the
# library's own recorder writes it, into the directory the console was already pointed at
# (kept_by_the_console) and only when the console was asked to record (agent_session.py:1042): a
# pointer to a file livekit will not write is a lie, so None.
# Decided before the session exists, so the bridge is born knowing the pointer call.summary will
# carry, and the directory is composed only for a call that is going to fill it.
def recording_path(job: JobContext, call: str, keeping: Keeping) -> Path | None:
    """The file this call's audio will be in, or None when none is kept."""
    return kept_by_the_job(job, keeping(call))


def kept_by_the_job(job: JobContext, destination: Path) -> Path | None:
    """The file this call's audio will be in, or None when nobody is going to write one."""
    console = _legacy.AgentsConsole.get_instance()
    if console.enabled:
        return audio_in(job.session_directory) if console.record else None
    return audio_in(destination)


def records_itself() -> bool:
    """Whether livekit's own recorder writes this file: only the console, which has no room."""
    return _legacy.AgentsConsole.get_instance().enabled


def asked_of_the_session(recording: Path | None) -> RecordingOptions | bool:
    """What `AgentSession.start(record=…)` is told: nothing on a box, where egress writes it."""
    if recording is None or not records_itself():
        return False
    return AUDIO_ONLY
