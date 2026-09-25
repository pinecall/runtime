"""A voice vendor's own list of voices, asked of the vendor: what a person picks a voice from."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from livekit.plugins.cartesia.constants import API_AUTH_HEADER, API_VERSION, API_VERSION_HEADER

from pinecall.providers.catalog import canonical
from pinecall.providers.language import primary
from pinecall.providers.registry import Asked, a_key
from pinecall.providers.tts.voices import VOICES

# Cartesia is the one vendor whose catalogue is read from the vendor: its ids are uuids nobody
# remembers, and it has a hundred voices in Spanish alone, so a picker that offered three names
# would hide every one of them. ElevenLabs answers with the names this build curates, because a
# premade is the only voice that exists in every workspace (voices.py says why). Every other vendor
# is refused by name, and its voice is still that vendor's own id typed into the setting. The
# catalogue row says which is which (api/providers.py, `voices_listed`), so a screen never keeps
# this list of its own.
LISTED: tuple[str, ...] = ("cartesia", "elevenlabs")
CARTESIA = "https://api.cartesia.ai"
PAGE = 100
# A vendor that pages forever is a bug at the vendor, not a reason to hang the screen that asked.
PAGES_AT_MOST = 20

NOT_LISTED = "this build lists no voices for {vendor}: its voice is the vendor's own id"
UNREACHABLE = "{vendor} did not list its voices: {why}"


@dataclass(frozen=True)
class ShelvedVoice:
    """One voice as a picker shows it: the id the setting takes, and what a person chooses by."""

    id: str
    name: str
    language: str
    description: str = ""
    gender: str = ""
    # Where the accent is from, as the vendor says it: `ES` is Spain and `MX` is Mexico, which is
    # the difference a caller hears first and the one a language code does not carry.
    country: str = ""
    accent: str = ""


class ShelfUnreachable(Exception):
    """The vendor was asked for its voices and did not answer with them: its status, or none."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NotListed(Exception):
    """A vendor whose catalogue this build does not read."""


class Shelf:
    """Every vendor catalogue this build reads, over one HTTP client the caller owns."""

    def __init__(self, http: httpx.AsyncClient, *, cartesia: str = CARTESIA) -> None:
        self._http = http
        self._cartesia = cartesia

    async def voices(self, vendor: str, language: str | None, asked: Asked) -> list[ShelvedVoice]:
        """The vendor's voices in that language, on the org's key or the box's, or a refusal."""
        named = canonical(vendor)
        wanted = primary(language)
        if named == "elevenlabs":
            return _curated(named, wanted)
        if named != "cartesia":
            raise NotListed(NOT_LISTED.format(vendor=named))
        return await self._cartesia_voices(wanted, a_key(named, asked))

    async def _cartesia_voices(self, language: str | None, key: str) -> list[ShelvedVoice]:
        # The vendor's `language` filter also lets through voices of other languages, so every row
        # is judged again here: a picker for a Spanish agent offering an English voice is the bug.
        shelved: list[ShelvedVoice] = []
        after: str | None = None
        for _ in range(PAGES_AT_MOST):
            page = await self._a_cartesia_page(language, key, after)
            shelved.extend(_cartesia_voices_of(page))
            after = page.get("next_page")
            if not page.get("has_more") or not after:
                break
        return [voice for voice in shelved if language is None or voice.language == language]

    async def _a_cartesia_page(self, language: str | None, key: str, after: str | None) -> Any:
        params: dict[str, str | int] = {"limit": PAGE}
        if language:
            params["language"] = language
        if after:
            params["starting_after"] = after
        headers = {API_AUTH_HEADER: key, API_VERSION_HEADER: API_VERSION}
        try:
            answer = await self._http.get(
                f"{self._cartesia}/voices", params=params, headers=headers
            )
            answer.raise_for_status()
        except httpx.HTTPStatusError as refused:
            status = refused.response.status_code
            raise ShelfUnreachable(
                UNREACHABLE.format(vendor="cartesia", why=status), status
            ) from refused
        except httpx.HTTPError as broke:
            raise ShelfUnreachable(UNREACHABLE.format(vendor="cartesia", why=broke)) from broke
        return answer.json()


# A row with no id is nothing a setting could take, so it is passed over rather than answered as
# a 500: the vendor's page is the vendor's, and one odd row must not empty the whole list.
def _cartesia_voices_of(page: Any) -> list[ShelvedVoice]:
    """The voices of one page, as the vendor wrote them."""
    return [_a_cartesia_voice(row) for row in page.get("data", []) if row.get("id")]


def _a_cartesia_voice(row: dict[str, Any]) -> ShelvedVoice:
    accents: list[dict[str, Any]] = row.get("accents") or [{}]
    return ShelvedVoice(
        id=str(row["id"]),
        name=str(row.get("name") or row["id"]),
        language=primary(str(row.get("language") or "")) or "",
        description=str(row.get("description") or ""),
        gender=str(row.get("gender") or ""),
        country=str(row.get("country") or ""),
        accent=str(accents[0].get("accent") or ""),
    )


def _curated(vendor: str, language: str | None) -> list[ShelvedVoice]:
    return [
        ShelvedVoice(id=name, name=name, language=voice.language)
        for name, voice in sorted(VOICES.items())
        if voice.vendor == vendor and (language is None or voice.language == language)
    ]
