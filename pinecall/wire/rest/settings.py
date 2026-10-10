"""The bodies of the settings doors: an agent's tuning and lexicon, versioned, and its canary."""

from pydantic import Field

from pinecall.domain.names import Env, JsonObject
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
    language: str | None = None
    greeting: GreetingConfig | None = None
    hangup: HangupConfig | None = None
    turn: TurnConfig | None = None
    memory: MemoryConfig | None = None
    record: bool | None = None
    max_duration_s: int | None = None
    llm_timeout_s: float | None = None
    temperature: float | None = None
    # Each stage's plugin class and its keyword arguments, the org's own key alone running them.
    tts_builds: str | None = None
    tts_options: JsonObject | None = None
    stt_builds: str | None = None
    stt_options: JsonObject | None = None
    llm_builds: str | None = None
    llm_options: JsonObject | None = None
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
    # The settings the class holding the agent declares itself, by the declaration's names
    # (`voice`, `llm`, `docs`…): each wins over these, which may not change it while it does.
    fixed: list[str] = Field(default_factory=list[str])


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


# A share is whole percents: 0 keeps the version off every call without clearing the canary.
class PutCanaryRequest(WireModel):
    """PUT /v1/agents/{slug}/settings/canary: the version, its share of the calls, why, whose."""

    version: int = Field(ge=1)
    share: int = Field(ge=0, le=100)
    note: str | None = None
    team: bool = False


class CanaryQuery(WireModel):
    """The query string of a canary's read or clearing: whose scope."""

    team: bool = False


class CanaryRow(WireModel):
    """The canary a scope stands on: which version takes how many calls in a hundred, by whom."""

    holder: str
    version: int
    share: int
    author: str
    note: str | None
    set_at: float


class CanaryResponse(WireModel):
    """GET, PUT and DELETE /v1/agents/{slug}/settings/canary: the scope's canary, or null."""

    world: Env
    holder: str
    canary: CanaryRow | None


class LexiconBody(WireModel):
    """The agent's lexicon: how the voice says its words, and the words the ears must know."""

    said: list[Pronunciation]
    heard: list[str]


class LexiconRow(WireModel):
    """One kept version of the agent's lexicon."""

    holder: str
    version: int
    author: str
    note: str | None
    set_at: float
    lexicon: LexiconBody


class LexiconResponse(WireModel):
    """GET /v1/agents/{slug}/lexicon: yours, the team's and production's newest, or null."""

    world: Env
    yours: LexiconRow | None
    team: LexiconRow | None
    production: LexiconRow | None
    # Which of `says` and `hears` the class holding the agent declares itself: it wins over these.
    fixed: list[str] = Field(default_factory=list[str])


class PutLexiconRequest(WireModel):
    """PUT /v1/agents/{slug}/lexicon: the whole lexicon, the version read, why, whose scope."""

    lexicon: LexiconBody
    if_version: int | None = None
    note: str | None = None
    team: bool = False


class LexiconHistoryResponse(WireModel):
    """GET /v1/agents/{slug}/lexicon/history: one scope's versions, newest first."""

    world: Env
    holder: str
    rows: list[LexiconRow]


class CallSettingsResponse(WireModel):
    """GET /v1/calls/{call}/settings: the tuning and lexicon the call was built on."""

    config_version: int | None
    lexicon_version: int | None
    config: SettingsRow | None
    lexicon: LexiconRow | None
    # The call ran a canary's version, picked for its share of the calls.
    canary: bool = False
