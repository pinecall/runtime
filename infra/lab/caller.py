"""caller.pcap: one caller's turn as RTP (PCMU, 20 ms a packet), for SIPp's play_pcap_audio."""

# 2.5 s of a voiced tone shaped like speech (four harmonics, a syllable-rate envelope), then 7.5 s
# of silence: caller.xml plays it once a turn. SIPp sends the payloads to the call's own media
# address and port, so the addresses written here are never read.
#
# Run: uv run --with numpy python caller.py   (writes caller.pcap beside it)

import struct
from pathlib import Path

import numpy as np

RATE = 8000
SPOKEN_S = 2.5
QUIET_S = 7.5
PACKET = 160  # samples in 20 ms
PCMU = 0
LINKTYPE_ETHERNET = 1


def _voice() -> np.ndarray:
    t = np.arange(int(SPOKEN_S * RATE)) / RATE
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)
    pitch = 180 + 25 * np.sin(2 * np.pi * 0.5 * t)
    voiced = sum(np.sin(2 * np.pi * pitch * h * t) / h for h in (1, 2, 3, 4))
    return np.concatenate([5000 * envelope * voiced, np.zeros(int(QUIET_S * RATE))])


SEGMENT_ENDS = np.array([0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF])


# The G.711 encoder of CPython's audioop (st_14linear2ulaw), which 3.13 removed: the same bytes.
def _ulaw(samples: np.ndarray) -> bytes:
    """G.711 mu-law of 16-bit samples."""
    fourteen = samples.astype(np.int32) >> 2
    negative = fourteen < 0
    mask = np.where(negative, 0x7F, 0xFF)
    magnitude = np.minimum(np.where(negative, -fourteen, fourteen), 8159) + 33
    segment = np.searchsorted(SEGMENT_ENDS, magnitude)
    coded = (np.minimum(segment, 7) << 4) | ((magnitude >> (segment + 1)) & 0x0F)
    coded = np.where(segment >= len(SEGMENT_ENDS), 0x7F, coded)
    return (coded ^ mask).astype(np.uint8).tobytes()


def _frame(seq: int, payload: bytes) -> bytes:
    rtp = struct.pack(">BBHII", 0x80, PCMU, seq & 0xFFFF, seq * PACKET, 0x1234ABCD) + payload
    udp = struct.pack(">HHHH", 6000, 6000, 8 + len(rtp), 0) + rtp
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0, bytes(4), bytes(4))
    return bytes(12) + b"\x08\x00" + ip + udp


def main() -> None:
    """Write caller.pcap."""
    audio = _ulaw(_voice().astype(np.int16))
    written = [struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, LINKTYPE_ETHERNET)]
    for seq in range(len(audio) // PACKET):
        frame = _frame(seq, audio[seq * PACKET : (seq + 1) * PACKET])
        at_us = seq * 20_000
        written.append(
            struct.pack("<IIII", at_us // 1_000_000, at_us % 1_000_000, len(frame), len(frame))
        )
        written.append(frame)
    (Path(__file__).parent / "caller.pcap").write_bytes(b"".join(written))


if __name__ == "__main__":
    main()
