"""The primary subtag of a declared language, by livekit's own normaliser: `es-ES` is `es` here."""

from __future__ import annotations

from livekit.agents.language import LanguageCode


# An agent declares its language as a free string (types/agent.py) and people write it every way:
# `es`, `es-ES`, `en_US`, `spanish`. A vendor files a voice under the base code alone, and the
# plugins send that base code — livekit's LanguageCode is what they normalise with, so it is what
# this runtime normalises with too, and there is no second reading of a language anywhere here.
def primary(language: str | None) -> str | None:
    """`es-ES`, `en_US`, `spanish` and `es` are one language: its base code, or None for none."""
    return None if not language or not language.strip() else LanguageCode(language).language
