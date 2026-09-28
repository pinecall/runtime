"""The bodies of the providers doors: the catalogue, the org's own keys, the voices, a sample."""

from typing import Literal

from pinecall.domain.names import JsonObject
from pinecall.wire.frames import WireModel

type Availability = Literal["yours", "offered", "bring your own", "broken"]


class ProviderRow(WireModel):
    """One installed vendor: what it does, whose key runs it for this org, and why not."""

    name: str
    does: list[Literal["llm", "stt", "tts"]]
    availability: Availability
    ready: bool
    broken: str | None
    voices_listed: bool


class Catalogue(WireModel):
    """GET /v1/providers: every vendor installed, the defaults per stage, the models named."""

    providers: list[ProviderRow]
    defaults: dict[str, str]
    models: dict[str, list[str]]


class VendorsResponse(WireModel):
    """GET /v1/provider-keys: the vendors the org brought its own credentials for, never one."""

    vendors: list[str]


# One key, or the whole object a plugin's constructor takes (`speech_key` and `speech_region`).
class ProviderKeyRequest(WireModel):
    """PUT /v1/provider-keys/{vendor}: the org's own credentials for one vendor."""

    key: str | None = None
    credentials: JsonObject | None = None


class ListedVoice(WireModel):
    """One voice as the picker shows it: the id the `voice` setting takes, and what to choose by."""

    id: str
    name: str
    language: str
    description: str
    gender: str
    country: str
    accent: str


class VoicesListed(WireModel):
    """GET /v1/voices?tts=&language=: a vendor's own voices, in the vendor's order."""

    tts: str
    language: str | None
    voices: list[ListedVoice]


class VoiceSampleRequest(WireModel):
    """POST /v1/voices/sample: which vendor, voice, model and words to hear; the answer is WAV."""

    tts: str
    voice: str
    model: str | None = None
    language: str | None = None
    text: str | None = None
