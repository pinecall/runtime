"""A recording sealed under its own key: AES-GCM a chunk at a time, so a range opens on its own."""

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pinecall.domain.errors import UpstreamFailed

# What a sealed file starts with, then the 8 random bytes every chunk's nonce begins with.
MAGIC = b"PCSEAL1\n"

PREFIX_BYTES = 8

# A chunk of the recording as recorded: a range opens the chunks it covers and no other, and a
# read holds one at a time. 64 KiB is some seconds of a call's audio.
CHUNK = 64 * 1024

TAG = 16

# The last chunk is sealed as the last, so a file cut short never opens as a shorter recording.
LAST, MORE = b"\x01", b"\x00"

KEY_BITS = 256

BROKEN = "the sealed recording of {call} does not open: {why}"

# Reads `length` bytes of the sealed object from `offset`, in whatever pieces its source yields.
type Read = Callable[[int, int], AsyncIterator[bytes]]


@dataclass(frozen=True)
class Sealed:
    """A sealed recording where it is kept: its size as kept, its nonces' prefix, its reader."""

    call: str
    size: int
    prefix: bytes
    read: Read


@dataclass(frozen=True)
class Span:
    """The bytes of the recording as recorded that a reader asked for, first and last included."""

    first: int
    last: int

    @property
    def length(self) -> int:
        """How many bytes the span holds."""
        return self.last - self.first + 1


# The format's name and the nonces' prefix, then each chunk and its tag.
HEADER = len(MAGIC) + PREFIX_BYTES

SEALED_CHUNK = CHUNK + TAG


def new_key() -> bytes:
    """A recording's own key."""
    return AESGCM.generate_key(bit_length=KEY_BITS)


# Blocking, for a thread: the file is read and written a chunk at a time, never whole.
def seal_file(plain: Path, sealed: Path, key: bytes) -> None:
    """Write `sealed` from `plain` under the key, then remove `plain`."""
    cipher = AESGCM(key)
    prefix = os.urandom(PREFIX_BYTES)
    part = sealed.with_name(f"{sealed.name}.part")
    with plain.open("rb") as source, part.open("wb") as target:
        target.write(MAGIC + prefix)
        chunk, index = source.read(CHUNK), 0
        while True:
            following = source.read(CHUNK)
            flag = MORE if following else LAST
            target.write(cipher.encrypt(_nonce(prefix, index), chunk, flag))
            if not following:
                break
            chunk, index = following, index + 1
        target.flush()
        os.fsync(target.fileno())
    part.rename(sealed)
    plain.unlink()


def recorded_size(sealed_size: int) -> int:
    """How many bytes the recording had, from its sealed object's size."""
    full, rest = divmod(sealed_size - HEADER, SEALED_CHUNK)
    return full * CHUNK + max(rest - TAG, 0)


def span_of(byte_range: str | None, size: int) -> Span | None:
    """The one range a player asked for within the recording; None for the whole of it."""
    if byte_range is None or not byte_range.startswith("bytes=") or "," in byte_range:
        return None
    first, _, last = byte_range.removeprefix("bytes=").strip().partition("-")
    if not (first or last) or not all(bound.isdigit() for bound in (first, last) if bound):
        return None
    if not first:
        return Span(max(size - int(last), 0), size - 1)
    # A range that ends before it starts is no range: the whole is answered, as HTTP says.
    if last and int(last) < int(first):
        return None
    return Span(int(first), min(int(last), size - 1) if last else size - 1)


async def opened(sealed: Sealed, key: bytes, span: Span) -> AsyncIterator[bytes]:
    """The span's bytes as recorded, opening only the chunks it covers, one at a time."""
    chunks = -(-(sealed.size - HEADER) // SEALED_CHUNK)
    first, last = span.first // CHUNK, span.last // CHUNK
    start = HEADER + first * SEALED_CHUNK
    end = min(HEADER + (last + 1) * SEALED_CHUNK, sealed.size)
    cipher = AESGCM(key)
    index = first
    async for piece in _in_chunks(sealed.read(start, end - start)):
        flag = LAST if index == chunks - 1 else MORE
        try:
            plain = cipher.decrypt(_nonce(sealed.prefix, index), piece, flag)
        except InvalidTag:
            raise UpstreamFailed(BROKEN.format(call=sealed.call, why=f"chunk {index}")) from None
        low = span.first - index * CHUNK if index == first else 0
        high = span.last - index * CHUNK + 1 if index == last else len(plain)
        yield plain[low:high]
        index += 1


async def on_disk(call: str, path: Path) -> Sealed | None:
    """The sealed file at the path, read a chunk at a time off the loop; None when absent."""
    try:
        header, size = await asyncio.to_thread(_head_of, path)
    except FileNotFoundError:
        return None
    if not header.startswith(MAGIC):
        raise UpstreamFailed(BROKEN.format(call=call, why=f"{path} is not a sealed recording"))

    async def read(offset: int, length: int) -> AsyncIterator[bytes]:
        with await asyncio.to_thread(path.open, "rb") as file:
            await asyncio.to_thread(file.seek, offset)
            left = length
            while left > 0:
                piece = await asyncio.to_thread(file.read, min(SEALED_CHUNK, left))
                if not piece:
                    return
                left -= len(piece)
                yield piece

    return Sealed(call, size, header[len(MAGIC) :], read)


def _head_of(path: Path) -> tuple[bytes, int]:
    with path.open("rb") as file:
        return file.read(HEADER), path.stat().st_size


def _nonce(prefix: bytes, index: int) -> bytes:
    return prefix + index.to_bytes(4, "big")


async def _in_chunks(pieces: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    waiting = bytearray()
    async for piece in pieces:
        waiting += piece
        while len(waiting) >= SEALED_CHUNK:
            yield bytes(waiting[:SEALED_CHUNK])
            del waiting[:SEALED_CHUNK]
    if waiting:
        yield bytes(waiting)
