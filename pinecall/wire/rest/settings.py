"""The bodies of the settings doors: an agent's tuning and the org's lexicon, versioned."""

from pydantic import Field

from pinecall.domain.names import Env
from pinecall.wire.frames import WireModel
from pinecall.wire.parts import (
    DocsConfig,
    GreetingConfig,
    HangupConfig,
    MemoryConfig,
    Pronunciation,
    TurnConfig,
)


# Every field is optional: one left out is not set, and the runtime's own default stands for it.
class SettingsBody(WireModel):
    """An agent's settings as the org set them, per world and per scope."""

    voice: str | None = None
    tts: str | None = None
    tts_model: str | None = None
    stt: str | None = None
    llm: str | None = None
    greeting: GreetingConfig | None = None
    hangup: HangupConfig | None = None
    turn: TurnConfig | None = None
    memory: MemoryConfig | None = None
    record: bool | None = None
    max_duration_s: int | None = None
    knowledge: str | None = None
    bases: list[DocsConfig] | None = None


class SettingsRow(WireModel):
    """One kept version of an agent's settings: whose scope, which version, by whom, what."""

    holder: str
    version: int
    author: str
    note: str | None
    set_at: float
    config: SettingsBody


class SettingsResponse(WireModel):
    """GET /v1/agents/{slug}/settings: yours, the team's and production's newest, or null."""

    world: Env
    yours: SettingsRow | None
    team: SettingsRow | None
    production: SettingsRow | None


class PutSettingsRequest(WireModel):
    """PUT /v1/agents/{slug}/settings: the whole set, the version read, why, whose scope."""

    config: SettingsBody
    if_version: int | None = None
    note: str | None = None
    team: bool = False


class HistoryQuery(WireModel):
    """The query string of a history: whose scope, and how many versions at most."""

    team: bool = False
    limit: int = Field(default=20, ge=1, le=200)


class SettingsHistoryResponse(WireModel):
    """GET /v1/agents/{slug}/settings/history: one scope's versions, newest first."""

    world: Env
    holder: str
    rows: list[SettingsRow]


class SettingsDiffResponse(WireModel):
    """GET /v1/agents/{slug}/settings/diff: this scope's newest against another's, by field."""

    ours: SettingsRow | None
    theirs: SettingsRow | None
    changed: list[str]


class RollbackSettingsRequest(WireModel):
    """POST /v1/agents/{slug}/settings/rollback: the version to bring back, as a new one."""

    version: int
    team: bool = False


class LexiconBody(WireModel):
    """The org's lexicon: how the voice says its words, and the words the ears must know."""

    said: list[Pronunciation]
    heard: list[str]


class LexiconRow(WireModel):
    """One kept version of the org's lexicon."""

    holder: str
    version: int
    author: str
    note: str | None
    set_at: float
    lexicon: LexiconBody


class LexiconResponse(WireModel):
    """GET /v1/lexicon: yours, the team's and production's newest, or null."""

    world: Env
    yours: LexiconRow | None
    team: LexiconRow | None
    production: LexiconRow | None


class PutLexiconRequest(WireModel):
    """PUT /v1/lexicon: the whole lexicon, the version read, why, whose scope."""

    lexicon: LexiconBody
    if_version: int | None = None
    note: str | None = None
    team: bool = False


class LexiconHistoryResponse(WireModel):
    """GET /v1/lexicon/history: one scope's versions, newest first."""

    world: Env
    holder: str
    rows: list[LexiconRow]


class CallSettingsResponse(WireModel):
    """GET /v1/calls/{call}/settings: the tuning and lexicon the call was built on."""

    config_version: int | None
    lexicon_version: int | None
    config: SettingsRow | None
    lexicon: LexiconRow | None
