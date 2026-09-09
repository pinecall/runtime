"""Where a call's audio is kept: the one place a recording's path is composed."""

from __future__ import annotations

from collections.abc import Callable
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
AUDIO_ONLY: RecordingOptions = {"audio": True, "traces": False, "logs": False, "transcript": False}

type Keeping = Callable[[str], Path | None]
"""What one process holds: a call id in, the directory its audio goes in out — or None."""


def keeping_for(settings: Settings) -> Keeping:
    """The process's way to a call's directory: the switch and the root read once at startup."""
    return partial(destination_for, settings=settings)


def destination_for(call: str, settings: Settings) -> Path | None:
    """The directory this call's audio goes in, created; None when the box records nothing."""
    if not settings.record:
        return None
    directory = Path(settings.recordings_root) / call
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def audio_in(directory: Path) -> Path:
    """The pointer the log carries: the file livekit will write inside that directory."""
    return directory / AUDIO_FILE


# The console is always the same room, so the room's name cannot tell two sessions apart and a
# second `pinecall talk` would record over the first. The moment can.
def a_console_session(now: datetime | None = None) -> str:
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


# An ordinary job's directory is a TemporaryDirectory livekit cleans up when the job ends
# (job.py:232,364), so an audio.ogg left there is gone before anybody reads the pointer. The job
# exposes the directory read-only (job.py:378) and RecorderIO writes wherever it points, so the one
# way to keep the file is the same move the console needed. Under a console the directory is
# already ours (kept_by_the_console), and the file is written only when the console was asked to
# record (agent_session.py:1042): a pointer to a file livekit will not write is a lie, so None.
def kept_by_the_job(job: JobContext, destination: Path) -> Path | None:
    """Point this job's recorder at our directory, and answer with the file it will write."""
    console = _legacy.AgentsConsole.get_instance()
    if console.enabled:
        return audio_in(job.session_directory) if console.record else None
    job._session_directory = destination  # pyright: ignore[reportPrivateUsage]
    return audio_in(destination)
