"""Where a call's audio is kept: the disk it was recorded on, or a bucket, under its org."""

import asyncio
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote

import httpx

from pinecall.domain.errors import UpstreamFailed
from pinecall.process.settings import Settings

# The recorder's file in the call's own directory: `<root>/<call>/audio.ogg` on the disk,
# `<org>/<call>/audio.ogg` in the bucket.
AUDIO_FILE = "audio.ogg"

# The machine's own identity, as the nightly backup's gcloud uses it: no key is ever stored.
IDENTITY_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)

OBJECTS = "https://storage.googleapis.com/storage/v1/b/{bucket}/o"

UPLOADS = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"

# An hour of a call is tens of megabytes: the default five seconds is a small file's. Under what
# a job is given to seal (worker/main.py SEALING_S): an upload that hangs leaves the file on the
# disk and the call is still sealed.
UPLOAD_TIMEOUT_S = 30.0

# What a player needs from the bucket's answer to seek.
PASSED_ON = ("content-length", "content-range", "accept-ranges")

# The answers a read passes on: the whole file, a range of it, a range past its end.
SERVED = frozenset({200, 206, 416})

# An org's erasure deletes its calls' objects this many at a time.
DELETED_AT_ONCE = 16

NO_IDENTITY = "the machine's identity gave no token for the recordings bucket: {why}"

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

    async def erase(self, org: str, calls: list[str]) -> int:
        """Remove each call's recording directory; how many calls had one."""
        return len(await asyncio.to_thread(_removed, self.root, calls))


@dataclass(frozen=True)
class Bucket:
    """Recordings moved to a bucket once written, the disk holding one only until it is."""

    root: Path
    name: str
    http: httpx.AsyncClient

    async def store(self, org: str, call: str, audio: Path) -> None:
        """Upload the file under the org, then remove it from the disk; raise if it stayed."""
        token = await _token(self.http)
        body = await asyncio.to_thread(audio.read_bytes)
        what = _object_name(org, call)
        try:
            answer = await self.http.post(
                UPLOADS.format(bucket=self.name),
                params={"uploadType": "media", "name": what},
                content=body,
                headers={"authorization": f"Bearer {token}", "content-type": "audio/ogg"},
                timeout=UPLOAD_TIMEOUT_S,
            )
        except httpx.HTTPError as unreachable:
            raise self._unreachable("an upload of", what, unreachable) from unreachable
        if answer.status_code != HTTPStatus.OK:
            raise self._refused("an upload of", what, answer)
        await asyncio.to_thread(_removed, self.root, [call])

    async def fetch(self, org: str, call: str, byte_range: str | None) -> Fetched | None:
        """The object's bytes, the range asked for when one is; None while it is not there."""
        token = await _token(self.http)
        what = _object_name(org, call)
        # The bytes as stored, so the length and the range passed on are the file's own.
        headers = {"authorization": f"Bearer {token}", "accept-encoding": "identity"}
        if byte_range is not None:
            headers["range"] = byte_range
        request = self.http.build_request(
            "GET", self._url_of(what), params={"alt": "media"}, headers=headers
        )
        try:
            answer = await self.http.send(request, stream=True)
        except httpx.HTTPError as unreachable:
            raise self._unreachable("a read of", what, unreachable) from unreachable
        if answer.status_code not in SERVED:
            await answer.aread()
            await answer.aclose()
            if answer.status_code == HTTPStatus.NOT_FOUND:
                return None
            raise self._refused("a read of", what, answer)
        kept = {name: answer.headers[name] for name in PASSED_ON if name in answer.headers}
        return Fetched(answer.status_code, kept, _streamed(answer))

    async def erase(self, org: str, calls: list[str]) -> int:
        """Delete each call's object, and its file where one stayed; how many calls had either."""
        token = await _token(self.http)
        deleted: set[str] = set()
        for start in range(0, len(calls), DELETED_AT_ONCE):
            chunk = calls[start : start + DELETED_AT_ONCE]
            gone = await asyncio.gather(*(self._deleted(token, org, call) for call in chunk))
            deleted |= {call for call, was_there in zip(chunk, gone, strict=True) if was_there}
        return len(deleted | await asyncio.to_thread(_removed, self.root, calls))

    async def _deleted(self, token: str, org: str, call: str) -> bool:
        what = _object_name(org, call)
        try:
            answer = await self.http.delete(
                self._url_of(what), headers={"authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as unreachable:
            raise self._unreachable("a delete of", what, unreachable) from unreachable
        if answer.status_code == HTTPStatus.NOT_FOUND:
            return False
        if answer.status_code != HTTPStatus.NO_CONTENT:
            raise self._refused("a delete of", what, answer)
        return True

    def _url_of(self, what: str) -> str:
        return f"{OBJECTS.format(bucket=self.name)}/{quote(what, safe='')}"

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

    def _unreachable(self, what: str, name: str, why: httpx.HTTPError) -> UpstreamFailed:
        return UpstreamFailed(
            UNREACHABLE.format(
                bucket=self.name, what=what, name=name, why=str(why) or type(why).__name__
            )
        )


type Recordings = Disk | Bucket


def recordings_of(settings: Settings, http: httpx.AsyncClient) -> Recordings:
    """The bucket PINECALL_RECORDINGS_BUCKET names, else the disk PINECALL_RECORDINGS is."""
    root = Path(settings.recordings_root)
    if settings.recordings_bucket is None:
        return Disk(root)
    return Bucket(root, settings.recordings_bucket, http)


def _object_name(org: str, call: str) -> str:
    return f"{org}/{call}/{AUDIO_FILE}"


async def _token(http: httpx.AsyncClient) -> str:
    try:
        answer = await http.get(IDENTITY_URL, headers={"metadata-flavor": "Google"})
    except httpx.HTTPError as unreachable:
        raise UpstreamFailed(
            NO_IDENTITY.format(why=str(unreachable) or type(unreachable).__name__)
        ) from None
    if answer.status_code != HTTPStatus.OK:
        raise UpstreamFailed(NO_IDENTITY.format(why=f"{answer.status_code} {answer.text[:200]}"))
    return str(answer.json()["access_token"])


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
