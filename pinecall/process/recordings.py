"""Where a call's audio is kept: the disk it was recorded on, or a bucket, under its org."""

import asyncio
import hashlib
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

import httpx

from pinecall.domain.errors import UpstreamFailed
from pinecall.process._objects import ObjectStore, object_store_of
from pinecall.process.sealed_audio import (
    BROKEN,
    HEADER,
    MAGIC,
    Sealed,
    Span,
    opened,
    recorded_size,
    span_of,
)
from pinecall.process.settings import Settings

# The recorder's file in the call's own directory: `<root>/<call>/audio.ogg` on the disk,
# `<org>/<call>/audio.ogg` in the bucket. Sealed under the call's own key, it is `audio.sealed`
# beside it (sealed_audio.py): the name says which a stored recording is.
AUDIO_FILE = "audio.ogg"

SEALED_FILE = "audio.sealed"

# An hour of a call is tens of megabytes: the default five seconds is a small file's. Under what
# a job is given to seal (worker/main.py SEALING_S): an upload that hangs leaves the file on the
# disk and the call is still sealed.
UPLOAD_TIMEOUT_S = 30.0

# What an upload reads of the file at a time: hashed first, then sent, and never held whole.
READ_BYTES = 256 * 1024

# What a player needs from the bucket's answer to seek.
PASSED_ON = ("content-length", "content-range", "accept-ranges")

# The answers a read passes on: the whole file, a range of it, a range past its end.
SERVED = frozenset({200, 206, 416})

# An org's erasure deletes its calls' objects this many at a time.
DELETED_AT_ONCE = 16

REFUSED = "the recordings bucket {bucket} answered {status} to {what} {name}: {text}"

UNREACHABLE = "the recordings bucket {bucket} did not answer {what} {name}: {why}"


@dataclass(frozen=True)
class Fetched:
    """A recording read from the bucket: the status and headers to pass on, and its bytes."""

    status: int
    headers: dict[str, str]
    body: AsyncIterator[bytes]


@dataclass(frozen=True)
class Disk:
    """Recordings kept on the disk they were recorded on: a box with no bucket, as always."""

    root: Path

    async def store(self, org: str, call: str, audio: Path) -> None:
        """Nothing to do: the recorder's file is where it is kept."""

    async def fetch(self, org: str, call: str, byte_range: str | None) -> Fetched | None:
        """None: the file on the disk is the recording, and the door serves it as a file."""

    async def sealed(self, org: str, call: str) -> Sealed | None:
        """None: a sealed file on the disk is read from where the call's summary says it is."""

    async def erase(self, org: str, calls: list[str]) -> int:
        """Remove each call's recording directory; how many calls had one."""
        return len(await asyncio.to_thread(_removed, self.root, calls))


@dataclass(frozen=True)
class Bucket:
    """Recordings moved to a bucket of the object store once written, the disk holding one until."""

    root: Path
    name: str
    objects: ObjectStore

    async def store(self, org: str, call: str, audio: Path) -> None:
        """Upload the file under the org, then remove it from the disk; raise if it stayed."""
        what = _object_name(org, call, audio.name)
        size, digest = await asyncio.to_thread(_measured, audio)
        url = self.objects.url_of(self.name, what)
        kind = "audio/ogg" if audio.name == AUDIO_FILE else "application/octet-stream"
        wanted = {"content-type": kind, "content-length": str(size)}
        request = self.objects.http.build_request(
            "PUT",
            url,
            headers=self.objects.signed("PUT", url, wanted, digest),
            content=_read(audio),
            timeout=UPLOAD_TIMEOUT_S,
        )
        answer = await self._sent(request, "an upload of", what)
        await answer.aread()
        await answer.aclose()
        if answer.status_code != HTTPStatus.OK:
            raise self._refused("an upload of", what, answer)
        await asyncio.to_thread(_removed, self.root, [call])

    async def fetch(self, org: str, call: str, byte_range: str | None) -> Fetched | None:
        """The recording's bytes, the range asked for when one is; None while it is not there."""
        return await self._fetched(_object_name(org, call, AUDIO_FILE), byte_range)

    async def sealed(self, org: str, call: str) -> Sealed | None:
        """The call's sealed object, read by ranges; None while it is not in the bucket."""
        what = _object_name(org, call, SEALED_FILE)
        first = await self._fetched(what, f"bytes=0-{HEADER - 1}")
        if first is None:
            return None
        header = b"".join([piece async for piece in first.body])
        size = first.headers.get("content-range", "").rpartition("/")[2]
        if not header.startswith(MAGIC) or not size.isdigit():
            raise UpstreamFailed(BROKEN.format(call=call, why=f"{what} is not a sealed recording"))

        async def read(offset: int, length: int) -> AsyncIterator[bytes]:
            part = await self._fetched(what, f"bytes={offset}-{offset + length - 1}")
            if part is None:
                raise UpstreamFailed(BROKEN.format(call=call, why=f"{what} went while it was read"))
            async for piece in part.body:
                yield piece

        return Sealed(call, int(size), header[len(MAGIC) :], read)

    async def _fetched(self, what: str, byte_range: str | None) -> Fetched | None:
        url = self.objects.url_of(self.name, what)
        wanted = {} if byte_range is None else {"range": byte_range}
        # The bytes as stored, so the length and the range passed on are the file's own.
        headers = {**self.objects.signed("GET", url, wanted), "accept-encoding": "identity"}
        answer = await self._sent(
            self.objects.http.build_request("GET", url, headers=headers), "a read of", what
        )
        if answer.status_code not in SERVED:
            await answer.aread()
            await answer.aclose()
            if answer.status_code == HTTPStatus.NOT_FOUND:
                return None
            raise self._refused("a read of", what, answer)
        kept = {name: answer.headers[name] for name in PASSED_ON if name in answer.headers}
        return Fetched(answer.status_code, kept, _streamed(answer))

    async def erase(self, org: str, calls: list[str]) -> int:
        """Delete each call's objects, and its file where one stayed; how many calls had either."""
        deleted: set[str] = set()
        for start in range(0, len(calls), DELETED_AT_ONCE):
            chunk = calls[start : start + DELETED_AT_ONCE]
            gone = await asyncio.gather(*(self._deleted(org, call) for call in chunk))
            deleted |= {call for call, was_there in zip(chunk, gone, strict=True) if was_there}
        return len(deleted | await asyncio.to_thread(_removed, self.root, calls))

    # S3 answers a delete of nothing as it answers a delete: each object is looked for first, so
    # the trail counts the recordings there were.
    async def _deleted(self, org: str, call: str) -> bool:
        kept = [await self._deleted_named(_object_name(org, call, name)) for name in NAMES]
        return any(kept)

    async def _deleted_named(self, what: str) -> bool:
        url = self.objects.url_of(self.name, what)
        existing = await self._response_to("HEAD", url, what)
        if existing.status_code == HTTPStatus.NOT_FOUND:
            return False
        if existing.status_code != HTTPStatus.OK:
            raise self._refused("a delete of", what, existing)
        answer = await self._response_to("DELETE", url, what)
        if answer.status_code not in {HTTPStatus.OK, HTTPStatus.NO_CONTENT}:
            raise self._refused("a delete of", what, answer)
        return True

    async def _response_to(self, method: str, url: str, what: str) -> httpx.Response:
        request = self.objects.http.build_request(
            method, url, headers=self.objects.signed(method, url, {})
        )
        answer = await self._sent(request, "a delete of", what)
        await answer.aread()
        await answer.aclose()
        return answer

    async def _sent(self, request: httpx.Request, what: str, name: str) -> httpx.Response:
        try:
            return await self.objects.http.send(request, stream=True)
        except httpx.HTTPError as unreachable:
            raise UpstreamFailed(
                UNREACHABLE.format(
                    bucket=self.name,
                    what=what,
                    name=name,
                    why=str(unreachable) or type(unreachable).__name__,
                )
            ) from unreachable

    def _refused(self, what: str, name: str, answer: httpx.Response) -> UpstreamFailed:
        return UpstreamFailed(
            REFUSED.format(
                bucket=self.name,
                status=answer.status_code,
                what=what,
                name=name,
                text=answer.text[:200],
            )
        )


# Every object a call's recording may be kept as.
NAMES = (AUDIO_FILE, SEALED_FILE)


type Recordings = Disk | Bucket


def recordings_of(settings: Settings, http: httpx.AsyncClient) -> Recordings:
    """The bucket PINECALL_RECORDINGS_BUCKET names in the object store, else the disk."""
    root = Path(settings.recordings_root)
    objects = object_store_of(settings, http)
    if settings.recordings_bucket is None or objects is None:
        return Disk(root)
    return Bucket(root, settings.recordings_bucket, objects)


def served_sealed(sealed: Sealed, key: bytes, byte_range: str | None) -> Fetched:
    """A sealed recording as a player reads it: the range it asked for, or the whole, opened."""
    size = recorded_size(sealed.size)
    span = span_of(byte_range, size)
    if span is not None and span.first >= size:
        return Fetched(
            HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
            {"content-range": f"bytes */{size}"},
            _nothing(),
        )
    whole = span or Span(0, size - 1)
    body = opened(sealed, key, whole) if size else _nothing()
    headers = {"accept-ranges": "bytes", "content-length": str(whole.length)}
    if span is None:
        return Fetched(HTTPStatus.OK, headers, body)
    shown = {**headers, "content-range": f"bytes {span.first}-{span.last}/{size}"}
    return Fetched(HTTPStatus.PARTIAL_CONTENT, shown, body)


def _object_name(org: str, call: str, name: str) -> str:
    return f"{org}/{call}/{name}"


def _measured(audio: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    with audio.open("rb") as source:
        while chunk := source.read(READ_BYTES):
            digest.update(chunk)
    return audio.stat().st_size, digest.hexdigest()


async def _read(audio: Path) -> AsyncIterator[bytes]:
    with audio.open("rb") as source:
        while chunk := await asyncio.to_thread(source.read, READ_BYTES):
            yield chunk


async def _nothing() -> AsyncIterator[bytes]:
    for piece in ():
        yield piece


async def _streamed(answer: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in answer.aiter_bytes():
            yield chunk
    finally:
        await answer.aclose()


# A recording is a directory named for its call (worker/_recorder.py recording_path).
def _removed(root: Path, calls: list[str]) -> set[str]:
    removed: set[str] = set()
    for call in calls:
        directory = root / call
        if directory.is_dir():
            shutil.rmtree(directory)
            removed.add(call)
    return removed
