"""Where each vendor answers a key with 200 or refuses it: the doctor's knock, never a call."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Knock:
    """One GET a vendor answers for free: the door, the header it reads the key from, the rest."""

    url: str
    header: str
    prefix: str = ""
    also: tuple[tuple[str, str], ...] = ()

    def headers(self, key: str) -> dict[str, str]:
        """The request headers, the key where this vendor reads it."""
        return {self.header: f"{self.prefix}{key}", **dict(self.also)}


# Keyed by the settings field, so the doctor names the variable and never the vendor. Each door
# is the cheapest one the vendor has: a listing, or the account itself — nothing is generated,
# transcribed or spoken, and a dead key is a 401 here instead of a silent agent on a live call.
KNOCKS: dict[str, Knock] = {
    "anthropic_api_key": Knock(
        "https://api.anthropic.com/v1/models",
        "x-api-key",
        also=(("anthropic-version", "2023-06-01"),),
    ),
    "openai_api_key": Knock("https://api.openai.com/v1/models", "authorization", "Bearer "),
    "soniox_api_key": Knock("https://api.soniox.com/v1/models", "authorization", "Bearer "),
    "deepgram_api_key": Knock("https://api.deepgram.com/v1/projects", "authorization", "Token "),
    "eleven_api_key": Knock("https://api.elevenlabs.io/v1/user", "xi-api-key"),
}
