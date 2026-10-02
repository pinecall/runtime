"""Three vendors faked on their own wire and timed like them: a load test that spends nothing."""

# The runtime's own plugins talk to this server unchanged: the providers row's tuning points each
# vendor's `base_url` here (infra/lab/README.md). What a worker spends on a call (the audio, the
# VAD, the streams) is spent for real; only the vendors' own work is not done, and not billed.
#
#   Deepgram Flux   WS   /v2/listen        hears the caller's audio: a turn starts when it is loud,
#                                          ends after 600 ms quiet, and is said as a fixed sentence
#   Cartesia        WS   /tts/websocket    speaks every sentence it is sent, 1.5x real time after
#                   POST /tts/bytes        120 ms, as a voiced tone shaped like speech
#   Anthropic       POST /v1/messages      answers in a stream, 450 ms to the first token, then
#                                          ~80 tokens a second
#
# Run: uv run --with aiohttp --with numpy python fake_vendors.py [port]   (8700 when unsaid)

import asyncio
import base64
import json
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from itertools import count

import numpy as np
from aiohttp import WSMsgType, web

LOUD = 500  # RMS of 16-bit samples above which the caller is speaking
QUIET_ENDS_TURN_S = 0.6
SPEECH_TO_START_S = 0.12
UPDATE_EVERY_S = 0.25
HEARD = "quiero un turno para mañana a las diez de la mañana por favor"
LLM_FIRST_TOKEN_S = 0.45
LLM_TOKEN_S = 1 / 80
ANSWERS = (
    "Claro, tengo un lugar mañana a las diez. ¿Se lo reservo a su nombre?",
    "Perfecto. ¿Me dice su nombre completo y un teléfono de contacto, por favor?",
    "Listo, quedó reservado para mañana a las diez. ¿Hay algo más en lo que pueda ayudarle?",
)
TTS_FIRST_AUDIO_S = 0.12
TTS_SPEED = 1.5  # seconds of audio sent per second, after the first
SECONDS_PER_CHAR = 0.065  # Spanish read aloud: ~15 characters a second
CHUNK_S = 0.04
turns = count()


# ---- Deepgram Flux -------------------------------------------------------------------------


def _rms(frame: bytes) -> float:
    samples = np.frombuffer(frame[: len(frame) // 2 * 2], dtype="<i2").astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0


def _turn_info(event: str, words: list[str], started: float, now: float, request: str) -> str:
    step = (now - started) / max(len(words), 1)
    return json.dumps(
        {
            "type": "TurnInfo",
            "event": event,
            "request_id": request,
            "turn_index": 0,
            "audio_window_start": started,
            "audio_window_end": now,
            "transcript": " ".join(words),
            "words": [
                {
                    "word": w,
                    "confidence": 0.97,
                    "start": started + i * step,
                    "end": started + (i + 1) * step,
                }
                for i, w in enumerate(words)
            ],
            "end_of_turn_confidence": 0.9 if event == "EndOfTurn" else 0.1,
        }
    )


async def flux(request: web.Request) -> web.WebSocketResponse:
    """Deepgram Flux's socket: the caller's audio in, a turn's start, updates and end out."""
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    rate = int(request.query.get("sample_rate", "16000"))
    req = str(uuid.uuid4())
    words = HEARD.split()
    heard_s = 0.0  # audio received so far, the clock the turn is told in
    loud_since: float | None = None
    quiet_since: float | None = None
    in_turn = False
    turn_start = 0.0
    last_update = 0.0
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            kind = json.loads(msg.data).get("type")
            if kind == "Configure":
                await ws.send_str(json.dumps({"type": "ConfigureSuccess", "request_id": req}))
            elif kind == "CloseStream":
                break
            continue
        if msg.type != WSMsgType.BINARY:
            break
        frame = msg.data
        heard_s += len(frame) / 2 / rate
        loud = _rms(frame) > LOUD
        if loud:
            quiet_since = None
            loud_since = heard_s if loud_since is None else loud_since
        else:
            loud_since = None
            quiet_since = heard_s if quiet_since is None else quiet_since
        if not in_turn and loud_since is not None and heard_s - loud_since >= SPEECH_TO_START_S:
            in_turn, turn_start, last_update = True, loud_since, heard_s
            await ws.send_str(_turn_info("StartOfTurn", words[:1], turn_start, heard_s, req))
        elif in_turn and quiet_since is not None and heard_s - quiet_since >= QUIET_ENDS_TURN_S:
            in_turn = False
            await ws.send_str(_turn_info("EndOfTurn", words, turn_start, quiet_since, req))
        elif in_turn and heard_s - last_update >= UPDATE_EVERY_S:
            last_update = heard_s
            so_far = min(len(words), 1 + int((heard_s - turn_start) / 0.3))
            await ws.send_str(_turn_info("Update", words[:so_far], turn_start, heard_s, req))
    await ws.close()
    return ws


# ---- Cartesia ------------------------------------------------------------------------------


_VOICE: dict[int, bytes] = {}
VOICE_LOOP_S = 4.0


def _speech(seconds: float, rate: int, phase: float) -> bytes:
    """A voiced tone with a syllable-rate envelope, as an encoder sees speech: cut from a loop."""
    if rate not in _VOICE:
        t = np.arange(int(VOICE_LOOP_S * rate)) / rate
        envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)
        pitch = 140 + 20 * np.sin(2 * np.pi * 0.5 * t)
        voiced = sum(np.sin(2 * np.pi * pitch * h * t) / h for h in (1, 2, 3, 4))
        _VOICE[rate] = (6000 * envelope * voiced).astype("<i2").tobytes()
    loop = _VOICE[rate]
    start = int((phase % VOICE_LOOP_S) * rate) * 2
    size = int(seconds * rate) * 2
    return (loop + loop)[start : start + size]


async def _spoken(text: str, rate: int, send: Callable[[bytes], Awaitable[None]]) -> None:
    """Send the audio of `text` in chunks, paced as the vendor sends it."""
    await asyncio.sleep(TTS_FIRST_AUDIO_S)
    total = max(len(text.strip()), 1) * SECONDS_PER_CHAR
    sent = 0.0
    while sent < total:
        piece = min(CHUNK_S, total - sent)
        await send(_speech(piece, rate, sent))
        sent += piece
        await asyncio.sleep(piece / TTS_SPEED)


async def _cartesia_speaks(
    ws: web.WebSocketResponse, context: str, packet: dict[str, object], rate: int
) -> None:
    """One packet of a context spoken: its audio, its words' times, and done when it is the last."""
    text = str(packet.get("transcript", ""))

    async def chunk(audio: bytes) -> None:
        await ws.send_str(
            json.dumps(
                {
                    "type": "chunk",
                    "context_id": context,
                    "done": False,
                    "data": base64.b64encode(audio).decode(),
                    "step_time": CHUNK_S * 1000,
                }
            )
        )

    if text.strip():
        await _spoken(text, rate, chunk)
        words = text.split()
        span = len(text.strip()) * SECONDS_PER_CHAR / max(len(words), 1)
        await ws.send_str(
            json.dumps(
                {
                    "type": "timestamps",
                    "context_id": context,
                    "word_timestamps": {
                        "words": words,
                        "start": [i * span for i in range(len(words))],
                        "end": [(i + 1) * span for i in range(len(words))],
                    },
                }
            )
        )
    if not packet.get("continue"):
        await ws.send_str(json.dumps({"type": "done", "context_id": context, "done": True}))


async def _after(before: asyncio.Task[None] | None, speaking: Awaitable[None]) -> None:
    """A context's packets spoken in the order they came."""
    if before is not None:
        await before
    await speaking


async def cartesia_ws(request: web.Request) -> web.WebSocketResponse:
    """Cartesia's socket: sentences in by context, audio chunks out at the vendor's pace."""
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    speaking: dict[str, asyncio.Task[None]] = {}
    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            break
        packet = json.loads(msg.data)
        context = packet.get("context_id", "")
        if packet.get("cancel"):
            task = speaking.pop(context, None)
            if task is not None:
                task.cancel()
            continue
        rate = packet.get("output_format", {}).get("sample_rate", 24000)
        spoken = _cartesia_speaks(ws, context, packet, rate)
        speaking[context] = asyncio.create_task(_after(speaking.get(context), spoken))
    for task in speaking.values():
        task.cancel()
    await ws.close()
    return ws


async def cartesia_bytes(request: web.Request) -> web.StreamResponse:
    """Cartesia's chunked HTTP: one sentence in, its audio streamed back."""
    packet = await request.json()
    rate = packet.get("output_format", {}).get("sample_rate", 24000)
    response = web.StreamResponse(headers={"Content-Type": "application/octet-stream"})
    await response.prepare(request)
    await _spoken(packet.get("transcript", ""), rate, response.write)
    await response.write_eof()
    return response


# ---- Anthropic -----------------------------------------------------------------------------


def _event(kind: str, data: dict[str, object]) -> bytes:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n".encode()


async def messages(request: web.Request) -> web.StreamResponse:
    """Anthropic's Messages API, streamed: one of three answers, token by token."""
    body = await request.json()
    prompt_chars = len(json.dumps(body.get("messages", []))) + len(
        json.dumps(body.get("system", ""))
    )
    answer = ANSWERS[next(turns) % len(ANSWERS)]
    tokens = [w + " " for w in answer.split()]
    usage = {
        "input_tokens": prompt_chars // 4,
        "output_tokens": 1,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    message = {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": body.get("model", "claude-haiku-4-5"),
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": usage,
    }
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    await asyncio.sleep(LLM_FIRST_TOKEN_S)
    await response.write(_event("message_start", {"message": message}))
    await response.write(
        _event("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}})
    )
    for token in tokens:
        await response.write(
            _event(
                "content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": token}}
            )
        )
        await asyncio.sleep(LLM_TOKEN_S)
    await response.write(_event("content_block_stop", {"index": 0}))
    await response.write(
        _event(
            "message_delta",
            {
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": len(tokens)},
            },
        )
    )
    await response.write(_event("message_stop", {}))
    await response.write_eof()
    return response


def app() -> web.Application:
    """The three vendors on one server."""
    served = web.Application()
    served.router.add_get("/v2/listen", flux)
    served.router.add_get("/tts/websocket", cartesia_ws)
    served.router.add_post("/tts/bytes", cartesia_bytes)
    served.router.add_post("/v1/messages", messages)
    served.router.add_get(
        "/", lambda _: web.Response(text=f"fake vendors, up since {time.ctime()}")
    )
    return served


if __name__ == "__main__":
    web.run_app(app(), port=int(sys.argv[1]) if len(sys.argv) > 1 else 8700)
