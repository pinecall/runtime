"""A worker's last minute: what its calls' job processes tell it, and what its heartbeat carries."""

import asyncio
import math
import socket
import tempfile
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, override

from pydantic import BaseModel, ConfigDict, ValidationError

from pinecall.process.settings import Settings
from pinecall.wire.rest.calls import BatchedEntry

# What the heartbeat carries is the last minute's, as the roster judges it.
WINDOW_S = 60.0


# A busy minute is thousands of turns: past this many, the oldest are forgotten early.
MOST_KEPT = 10_000


# A p95 is read off this many turns at least; fewer, and the heartbeat carries none.
TURNS_FOR_A_P95 = 5


# How a call that failed ended; one datagram per thing told, and a datagram lost is uncounted.
AN_ERROR = "error"


class JobReport(BaseModel):
    """One thing a job tells its worker: a turn's first audio, an error, or how its call ended."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    first_audio_s: float | None = None
    error: bool = False
    ended: str | None = None


@dataclass(frozen=True, slots=True)
class Minute:
    """A worker's calls over the last minute: ended, failed, error entries, and first audio."""

    ended: int
    # Of the calls that ended, those that ended in an error.
    failed: int
    errors: int
    turns: int
    first_audio_p95_s: float | None


class LastMinute:
    """What the worker's job processes told it, kept for a minute, in the worker's main process."""

    def __init__(self) -> None:
        """Nothing heard yet."""
        self.first_audio: deque[tuple[float, float]] = deque(maxlen=MOST_KEPT)
        self.errors: deque[float] = deque(maxlen=MOST_KEPT)
        self.ended: deque[tuple[float, bool]] = deque(maxlen=MOST_KEPT)

    def heard(self, message: bytes, now: float) -> None:
        """One datagram from a job; one that does not read is dropped."""
        try:
            report = JobReport.model_validate_json(message)
        except ValidationError:
            return
        if report.first_audio_s is not None:
            self.first_audio.append((now, report.first_audio_s))
        if report.error:
            self.errors.append(now)
        if report.ended is not None:
            self.ended.append((now, report.ended == AN_ERROR))

    def of(self, now: float) -> Minute:
        """The minute up to now."""
        since = now - WINDOW_S
        while self.first_audio and self.first_audio[0][0] < since:
            self.first_audio.popleft()
        while self.errors and self.errors[0] < since:
            self.errors.popleft()
        while self.ended and self.ended[0][0] < since:
            self.ended.popleft()
        waits = sorted(value for _, value in self.first_audio)
        return Minute(
            ended=len(self.ended),
            failed=sum(1 for _, failed in self.ended if failed),
            errors=len(self.errors),
            turns=len(waits),
            first_audio_p95_s=_p95(waits) if len(waits) >= TURNS_FOR_A_P95 else None,
        )


class _Heard(asyncio.DatagramProtocol):
    def __init__(self, minute: LastMinute) -> None:
        self.minute = minute

    @override
    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        del addr
        self.minute.heard(data, asyncio.get_running_loop().time())


# Private to the unit on the box (PrivateTmp=yes); the health port tells two workers of a machine
# apart, as it must already, since each binds its own.
def measures_path(settings: Settings) -> Path:
    """Where a worker hears its jobs: a datagram socket its main process binds."""
    name = f"pinecall-{settings.fleet}-{settings.worker_http_port}.measures"
    return Path(tempfile.gettempdir()) / name


# Bound before livekit starts its processes; a leftover of a worker that died is taken over.
async def listening(path: Path, minute: LastMinute) -> asyncio.DatagramTransport:
    """Bind the socket the worker's jobs tell it through."""
    await asyncio.to_thread(path.unlink, missing_ok=True)
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: _Heard(minute), local_addr=str(path), family=socket.AF_UNIX
    )
    return transport


# Called on every batch a job sends: most carry none of the three, and send nothing.
def reported(path: Path, entries: Sequence[BatchedEntry]) -> None:
    """Tell the worker what a batch of its call's entries says: first audio, errors, an end."""
    messages = [message for entry in entries if (message := _message_of(entry)) is not None]
    if not messages:
        return
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
        sender.settimeout(0)
        for message in messages:
            try:
                sender.sendto(message, str(path))
            except OSError:
                # A worker not listening (a test, or its main process gone) loses the measure only.
                return


# Nearest rank, of values already sorted.
def _p95(values: Sequence[float]) -> float:
    return values[max(math.ceil(0.95 * len(values)) - 1, 0)]


def _message_of(entry: BatchedEntry) -> bytes | None:
    if entry.type == "turn.agent":
        measured = entry.data.get("metrics")
        waited = measured.get("e2e_latency") if isinstance(measured, dict) else None
        if isinstance(waited, int | float):
            return JobReport(first_audio_s=waited).model_dump_json().encode()
    if entry.type == AN_ERROR:
        return JobReport(error=True).model_dump_json().encode()
    if entry.type == "call.ended":
        reason = entry.data.get("reason")
        return JobReport(ended=reason if isinstance(reason, str) else "").model_dump_json().encode()
    return None
