"""The settings doors: an agent's tuning and lexicon per world and scope, and a call's own."""

import dataclasses
from dataclasses import replace
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import ValidationError

from pinecall.domain.agent import AgentConfig, Lexicon, Tuning, Version
from pinecall.domain.errors import DeclarationRefused, NotAllowed, NotFound
from pinecall.domain.names import Env
from pinecall.domain.scope import THE_ORGS_OWN, Scope
from pinecall.gateway._call_setup import keys_of
from pinecall.gateway._deps import Acting, CallsKey, GatewayDep, PipelineKey, ScopeDep, WordsKey
from pinecall.gateway._gateway import Gateway
from pinecall.log import queries
from pinecall.providers import catalog, credentials
from pinecall.providers.declared import apply_tuning
from pinecall.tenancy import scopes
from pinecall.tenancy.scopes import TUNING, Written
from pinecall.wire.parts import Pronunciation
from pinecall.wire.rest.settings import (
    CallSettingsResponse,
    HistoryQuery,
    LexiconBody,
    LexiconHistoryResponse,
    LexiconResponse,
    LexiconRow,
    PutLexiconRequest,
    PutSettingsRequest,
    RollbackSettingsRequest,
    SettingsBody,
    SettingsDiffResponse,
    SettingsHistoryResponse,
    SettingsResponse,
    SettingsRow,
)

router = APIRouter()


# A `words` key (a supervisor's) sets the opening's words, what is remembered and the agent's
# lexicon; the rest is the pipeline's, and the key is refused by the fields it touched.
PIPELINE_ONLY = (
    "voice",
    "tts",
    "tts_model",
    "stt",
    "llm",
    "hangup",
    "turn",
    "bases",
    "record",
    "max_duration_s",
)


NOT_WORDS = "{fields}: the pipeline's, and this key opens words alone"


NO_SUCH_VERSION = "no version {version} in this scope of {slug}"


NO_SUCH_CALL = "no call {call} in this org"


@router.get("/v1/agents/{slug}/settings", response_model_exclude_unset=True)
async def get_settings(
    slug: str, key: WordsKey, scope: ScopeDep, gateway: GatewayDep
) -> SettingsResponse:
    """The agent's settings as this key sees them: yours, the team's and production's."""
    return await _side_by_side(gateway, scope, slug, world=key.env)


# A whole replacement, guarded by the version it was read at; the read shows each scope's own
# newest and never the fallthrough, because if_version names the scope being written. A knob
# nobody set is left out of the answer, never sent as null: the body's fields are optional.
@router.put("/v1/agents/{slug}/settings", response_model_exclude_unset=True)
async def put_settings(
    slug: str, body: PutSettingsRequest, key: WordsKey, scope: ScopeDep, gateway: GatewayDep
) -> SettingsResponse:
    """The agent's next version in this scope or the team's, checked as a call would build it."""
    pool = gateway.connections.pool
    written_to = _written_to(scope, team=body.team)
    wanted = tuning_of(body.config)
    if "pipeline" not in key.bearer.key.scopes:
        kept = await scopes.tuning_side_by_side(pool, written_to, slug)
        newest = kept.team if written_to.holder == THE_ORGS_OWN else kept.yours
        wanted = _words_only(wanted, Tuning() if newest is None else newest.value)
    await _checked(gateway, written_to, slug, wanted)
    written = Written(author=_author(key), note=body.note, if_version=body.if_version)
    await scopes.put_tuning(pool, written_to, slug, wanted, written)
    return await _side_by_side(gateway, scope, slug, world=key.env)


@router.get("/v1/agents/{slug}/settings/history", response_model_exclude_unset=True)
async def settings_history(
    slug: str,
    _key: WordsKey,
    scope: ScopeDep,
    gateway: GatewayDep,
    query: Annotated[HistoryQuery, Query()],
) -> SettingsHistoryResponse:
    """One scope's versions of the agent's settings, newest first."""
    written_to = _written_to(scope, team=query.team)
    rows = await scopes.tuning_history(
        gateway.connections.pool, written_to, slug, limit=query.limit
    )
    return SettingsHistoryResponse(
        world=scope.env, holder=written_to.holder, rows=[settings_row(row) for row in rows]
    )


@router.get("/v1/agents/{slug}/settings/diff", response_model_exclude_unset=True)
async def settings_diff(
    slug: str,
    _key: WordsKey,
    scope: ScopeDep,
    gateway: GatewayDep,
    against: Annotated[Literal["team", "production"], Query()] = "production",
) -> SettingsDiffResponse:
    """This key's scope's newest against the team's or production's, and which fields differ."""
    kept = await scopes.tuning_side_by_side(gateway.connections.pool, scope, slug)
    ours = kept.yours if scope.holder else kept.team
    theirs = kept.production if against == "production" else kept.team
    return SettingsDiffResponse(
        ours=None if ours is None else settings_row(ours),
        theirs=None if theirs is None else settings_row(theirs),
        changed=_differing(ours, theirs),
    )


# A rollback writes a new version copying an old one; nothing is ever deleted.
@router.post("/v1/agents/{slug}/settings/rollback", response_model_exclude_unset=True)
async def rollback_settings(
    slug: str, body: RollbackSettingsRequest, key: PipelineKey, scope: ScopeDep, gateway: GatewayDep
) -> SettingsResponse:
    """An old version brought back as the scope's next one."""
    pool = gateway.connections.pool
    written_to = _written_to(scope, team=body.team)
    row = await scopes.tuning_at(pool, written_to, slug, body.version)
    if row is None:
        raise NotFound(NO_SUCH_VERSION.format(version=body.version, slug=slug))
    written = Written(author=_author(key), note=f"rollback to v{body.version}")
    await scopes.put_tuning(pool, written_to, slug, row.value, written)
    return await _side_by_side(gateway, scope, slug, world=key.env)


# By the versions the call's head row kept, never the current ones.
@router.get("/v1/calls/{call}/settings", response_model_exclude_unset=True)
async def call_settings(call: str, key: CallsKey, gateway: GatewayDep) -> CallSettingsResponse:
    """The exact settings and lexicon a call was built on."""
    pool = gateway.connections.pool
    kept = await queries.scope_of_call(pool, call)
    if kept is None or kept.scope is None or kept.scope.org != key.org:
        raise NotFound(NO_SUCH_CALL.format(call=call))
    versions = kept.versions
    config = (
        None
        if versions.config is None
        else await scopes.tuning_at(pool, kept.scope, kept.agent, versions.config)
    )
    words = (
        None
        if versions.lexicon is None
        else await scopes.lexicon_at(pool, kept.scope, kept.agent, versions.lexicon)
    )
    return CallSettingsResponse(
        config_version=versions.config,
        lexicon_version=versions.lexicon,
        config=None if config is None else settings_row(config),
        lexicon=None if words is None else lexicon_row(words),
    )


@router.get("/v1/agents/{slug}/lexicon")
async def get_lexicon(
    slug: str, key: WordsKey, scope: ScopeDep, gateway: GatewayDep
) -> LexiconResponse:
    """The agent's words as this key sees them: yours, the team's and production's."""
    return await _lexicon_side_by_side(gateway, scope, slug, world=key.env)


# A supervisor's key may write it: the words are theirs to fix.
@router.put("/v1/agents/{slug}/lexicon")
async def put_lexicon(
    slug: str, body: PutLexiconRequest, key: WordsKey, scope: ScopeDep, gateway: GatewayDep
) -> LexiconResponse:
    """The agent's next lexicon in this scope or the team's."""
    written_to = _written_to(scope, team=body.team)
    wanted = Lexicon(
        said={item.word: item.spoken for item in body.lexicon.said}, heard=tuple(body.lexicon.heard)
    )
    written = Written(author=_author(key), note=body.note, if_version=body.if_version)
    await scopes.put_lexicon(gateway.connections.pool, written_to, slug, wanted, written)
    return await _lexicon_side_by_side(gateway, scope, slug, world=key.env)


@router.get("/v1/agents/{slug}/lexicon/history")
async def lexicon_history(
    slug: str,
    _key: WordsKey,
    scope: ScopeDep,
    gateway: GatewayDep,
    query: Annotated[HistoryQuery, Query()],
) -> LexiconHistoryResponse:
    """One scope's versions of the agent's lexicon, newest first."""
    written_to = _written_to(scope, team=query.team)
    rows = await scopes.lexicon_history(
        gateway.connections.pool, written_to, slug, limit=query.limit
    )
    return LexiconHistoryResponse(
        world=scope.env, holder=written_to.holder, rows=[lexicon_row(row) for row in rows]
    )


# pydantic wraps the dataclass's own refusal; the person reads that sentence, not the report.
def tuning_of(body: SettingsBody) -> Tuning:
    """The body as the tuning a call is built from; refused in the shape's own words."""
    try:
        return TUNING.validate_python(body.model_dump(mode="python", exclude_none=True))
    except ValidationError as invalid:
        for error in invalid.errors():
            cause = error.get("ctx", {}).get("error")
            if isinstance(cause, DeclarationRefused):
                raise cause from invalid
        raise DeclarationRefused(str(invalid)) from invalid


def settings_row(kept: Version[Tuning]) -> SettingsRow:
    """A stored version as the doors send it."""
    return SettingsRow(
        holder=kept.holder,
        version=kept.version,
        author=kept.author,
        note=kept.note,
        set_at=kept.set_at.timestamp(),
        config=SettingsBody.model_validate(_body_of(kept.value)),
    )


def lexicon_row(kept: Version[Lexicon]) -> LexiconRow:
    """A stored lexicon as the doors send it."""
    return LexiconRow(
        holder=kept.holder,
        version=kept.version,
        author=kept.author,
        note=kept.note,
        set_at=kept.set_at.timestamp(),
        lexicon=LexiconBody(
            said=[
                Pronunciation(word=word, spoken=spoken) for word, spoken in kept.value.said.items()
            ],
            heard=list(kept.value.heard),
        ),
    )


# The settings are checked as a call would be built from them: the declaration when an app
# holds the agent, a bare one otherwise, its lexicon in the scope, and the vendors on the org's
# keys, so a vendor the box does not lend is refused here and not on the next call.
async def _checked(gateway: Gateway, scope: Scope, slug: str, wanted: Tuning) -> None:
    pool = gateway.connections.pool
    registration = gateway.sockets.of(scope, slug)
    declared = AgentConfig(slug=slug) if registration is None else registration.config
    configured = await catalog.providers(pool)
    words = await scopes.current(pool, scope, slug)
    config = apply_tuning(declared, wanted, words.lexicon, defaults=configured.defaults)
    keyring = await keys_of(pool, gateway.connections.vault, scope)
    credentials.pipeline(config, configured, keyring)


def _words_only(wanted: Tuning, newest: Tuning) -> Tuning:
    carried = {
        name: getattr(newest, name) for name in PIPELINE_ONLY if getattr(wanted, name) is None
    }
    touched = [
        name
        for name in PIPELINE_ONLY
        if getattr(wanted, name) is not None and getattr(wanted, name) != getattr(newest, name)
    ]
    opening = wanted.greeting
    if (
        opening is not None
        and opening != newest.greeting
        and (opening.reply is not None or opening.allow_interruptions is not None)
    ):
        touched = [*touched, "greeting.reply"]
    if touched:
        raise NotAllowed(NOT_WORDS.format(fields=", ".join(touched)))
    return dataclasses.replace(wanted, **carried)


def _differing(ours: Version[Tuning] | None, theirs: Version[Tuning] | None) -> list[str]:
    mine = {} if ours is None else _body_of(ours.value)
    yours = {} if theirs is None else _body_of(theirs.value)
    return sorted(name for name in set(mine) | set(yours) if mine.get(name) != yours.get(name))


async def _side_by_side(
    gateway: Gateway, scope: Scope, slug: str, *, world: Env
) -> SettingsResponse:
    kept = await scopes.tuning_side_by_side(gateway.connections.pool, scope, slug)
    return SettingsResponse(
        world=world,
        yours=None if kept.yours is None else settings_row(kept.yours),
        team=None if kept.team is None else settings_row(kept.team),
        production=None if kept.production is None else settings_row(kept.production),
    )


async def _lexicon_side_by_side(
    gateway: Gateway, scope: Scope, slug: str, *, world: Env
) -> LexiconResponse:
    kept = await scopes.lexicon_side_by_side(gateway.connections.pool, scope, slug)
    return LexiconResponse(
        world=world,
        yours=None if kept.yours is None else lexicon_row(kept.yours),
        team=None if kept.team is None else lexicon_row(kept.team),
        production=None if kept.production is None else lexicon_row(kept.production),
    )


# A key holding no agent writes the org's own scope: no call resolves in its holder's.
def _written_to(scope: Scope, *, team: bool) -> Scope:
    return replace(scope, holder=THE_ORGS_OWN) if team else scope


def _author(key: Acting) -> str:
    return key.bearer.key.subject or key.bearer.key.key_id


def _body_of(tuning: Tuning) -> dict[str, object]:
    dumped: dict[str, object] = TUNING.dump_python(tuning, mode="json", exclude_none=True)
    return dumped
