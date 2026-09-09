"""What counts as cutting the agent off: two words, and never two words of agreement."""

from __future__ import annotations

import re

# livekit's own InterruptionOptions.min_words defaults to 0 (voice/turn.py:194) — every syllable
# stops the agent. Two is the number a spoken call wants: one word is a cough, a "mm" or the tail
# of the agent's own audio coming back down a speakerphone. worker/session.py hands this to
# livekit, which does the counting (agent_activity.py:2146,2483); the stoplist below is ours,
# because livekit's own backchannel detector is the hosted "adaptive" mode a self-hosted box
# cannot reach (inference/interruption.py) — see docs/decisions/voice-bridge.md.
MIN_WORDS = 2

# What a person says while they are listening, not while they are taking the floor. A turn made
# only of these is a backchannel: the caller is saying "go on", and stopping the agent mid-sentence
# is the opposite of what they asked for. Spanish first, because the first tenants speak it.
BACKCHANNELS: frozenset[str] = frozenset(
    {
        "aha",
        "ajá",
        "ah",
        "ajam",
        "bien",
        "bueno",
        "claro",
        "dale",
        "eh",
        "em",
        "exacto",
        "hm",
        "hmm",
        "mm",
        "mmm",
        "ok",
        "okay",
        "perfecto",
        "sí",
        "si",
        "vale",
        "ya",
        "yeah",
        "yes",
        "uh",
        "uhu",
        "uhum",
    }
)

# Words as a person counts them: letters and digits, apostrophes inside a word kept, everything
# else a separator. Unicode-aware, so "sí" is one word and not two.
_A_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def words_of(text: str) -> list[str]:
    """The words in what was heard, lowercased, punctuation gone."""
    return [word.lower() for word in _A_WORD.findall(text)]


def is_a_backchannel(text: str) -> bool:
    """Whether these words only say that somebody is listening, and take no floor at all."""
    words = words_of(text)
    return bool(words) and all(word in BACKCHANNELS for word in words)
