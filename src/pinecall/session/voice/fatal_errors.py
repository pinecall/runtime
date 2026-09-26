"""A component failure whose cause cannot change, so the call ends instead of retrying at it."""

from __future__ import annotations

from livekit.agents import APIStatusError

# The answers a vendor gives to the request itself rather than to the moment: the key is wrong, the
# door is not there, the body is not accepted. Asking again changes nothing. 408, 429 and 5xx are
# absent on purpose — those are the moment, and livekit's own retry is right about them.
FOREVER: frozenset[int] = frozenset({400, 401, 402, 403, 404, 422})

# A websocket the vendor closed with a policy violation: the same request, refused before a byte of
# audio. ElevenLabs closes with it for an unknown voice_id, and livekit reads the close code into
# APIStatusError.status_code (plugins/elevenlabs/tts.py:847), where its own 4xx test never sees it
# (agents/_exceptions.py:75) — which is how one wrong voice became seven retries in twenty seconds.
POLICY_VIOLATION = 1008


def is_a_dead_end(error: BaseException) -> bool:
    """Whether this failure will answer the same way every time, so a retry is only time lost."""
    if not isinstance(error, APIStatusError):
        return False
    return error.status_code == POLICY_VIOLATION or error.status_code in FOREVER
