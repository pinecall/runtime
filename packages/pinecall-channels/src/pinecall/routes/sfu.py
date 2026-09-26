"""The media plane's address and key pair, read once off the settings by every client of it."""

from __future__ import annotations

from dataclasses import dataclass

from livekit import api

from pinecall.settings import Settings


# Four tables of this package talk to the SFU — the inbound trunks, the outbound ones, the
# dispatches, the rooms — and each read the same three settings and guarded the same pair. One
# reading: a gateway with no pair has no SFU, and each factory answers None from that alone.
@dataclass(frozen=True)
class Sfu:
    """Where livekit-server answers, and the API key pair this process speaks to it with."""

    url: str
    api_key: str
    api_secret: str

    @classmethod
    def of(cls, settings: Settings) -> Sfu | None:
        """The SFU this process may reach; None when the settings hold no key pair."""
        if settings.livekit_api_key and settings.livekit_api_secret:
            return cls(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)
        return None

    def api(self) -> api.LiveKitAPI:
        """One client, for one `async with`: livekit-api opens a session per client."""
        return api.LiveKitAPI(self.url, self.api_key, self.api_secret)
