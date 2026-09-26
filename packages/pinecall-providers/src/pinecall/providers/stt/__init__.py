"""Who hears: the ears of a call, one vendor per file, 16 kHz on every channel including PSTN."""

# Spanish first, English second: the languages a Pinecall caller actually speaks. An agent that
# declares its own language puts it in front, and English stays the fallback every caller mixes in.
from pinecall.providers.registry import Ears, Vendors

DEFAULT_HINTS = ("es", "en")

# The longest silence a caller is left waiting before the model calls the turn finished. Both
# plugins wait longer by default — soniox 2000 ms (soniox/stt.py:124), deepgram 3000
# (deepgram/stt_v2.py:97) — which is a held breath on a phone line.
MAX_SILENCE_MS = 1000

VENDORS: Vendors[Ears] = Vendors("stt", __name__)


def hints_for(language: str | None) -> list[str]:
    """The agent's own language first, then the two everybody here speaks, each named once."""
    ordered = (language, *DEFAULT_HINTS) if language else DEFAULT_HINTS
    return list(dict.fromkeys(hint for hint in ordered if hint))
