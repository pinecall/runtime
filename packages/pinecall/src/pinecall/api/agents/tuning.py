"""An agent's settings: what the org set over the class, per world and per corner, versioned."""

from __future__ import annotations

import dataclasses
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import TypeAdapter, ValidationError

from pinecall.api.deps import (
    CallIndexDep,
    CallsKeyDep,
    OrgsDep,
    PipelineKeyDep,
    RegistryDep,
    TuningDep,
    VaultDep,
    require_scopes,
)
from pinecall.auth.keys import KeyRecord, cannot_open, is_held_by
from pinecall.auth.request_scope import author_of
from pinecall.live.registry import NO_AGENT, Registry
from pinecall.orgs import Corners, settings_corners
from pinecall.orgs.tuning_resolution import tuning_json
from pinecall.orgs.tuning_store import HISTORY_LIMIT
from pinecall.orgs.vault import brought_by
from pinecall.providers.session_vendors import first_unlent_vendor
from pinecall.providers.tuned_declaration import apply_tuning
from pinecall.types import (
    HOLDING,
    PRODUCTION,
    THE_ORGS_OWN,
    AgentConfig,
    DeclarationRefused,
    Env,
    Kept,
    Lexicon,
    Tuning,
    parse_env,
    whose,
)
from pinecall_protocol.defs import Pronunciation
from pinecall_protocol.rest import (
    CallTuning,
    LexiconBody,
    LexiconRow,
    Rollback,
    TuningAnswer,
    TuningBody,
    TuningDiff,
    TuningHistory,
    TuningPut,
    TuningRow,
)

router = APIRouter()

# An agent's settings are read and set by two kinds of key: the developer's, which opens the
# pipeline and may move a vendor, and the floor's — a supervisor's, a manager's — which opens
# `words` and may set the opening's words, the lexicon and what is remembered, never a vendor.
# The doors open to either and ask inside which half a body touches (`words_only` below).
TuningKeyDep = Annotated[KeyRecord, Depends(require_scopes("pipeline", "words"))]

# The wire's body read into the shape, and the shape written back out: one adapter, so what a door
# accepts and what a row says are the same thing. orgs/tuning_resolution.py writes a column through
# it too.
TUNING: TypeAdapter[Tuning] = TypeAdapter(Tuning)

# What a `words` key may touch and what it may not: the vendors, the models, the cut of a turn and
# what the call reads from are the pipeline's. A words key's set carries those over untouched
# from what stands, and is refused by name the moment it would move one. An ABSENT knob is the one
# carried over, `bases` with the rest: an empty `bases` is a person taking the bases out, which is
# a move of the pipeline and is refused by name like any other.
# `record` is not a stage of the pipeline and sits in this list anyway: whether a call keeps its
# audio is the org's to decide and nobody's to change from the floor, which is exactly what this
# list is for, and so does how long a voice call may run. One list, so there is one place to look
# for what a words key cannot move.
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
NOT_WORDS = "{fields}: the pipeline's, and {refusal}"

NO_SUCH_VERSION = "no version {version} in this corner of {slug}"
NO_SUCH_CALL = "no call {call} in this org"


# ── the shapes, both ways ───────────────────────────────────────────────────────


def parse_tuning(body: TuningBody) -> Tuning:
    """The wire's body as the domain's shape, or 400 in the shape's own sentence."""
    try:
        return TUNING.validate_python(body.model_dump(mode="python", exclude_none=True))
    except ValidationError as invalid:
        raise HTTPException(400, _the_shapes_own_words(invalid)) from invalid


# pydantic wraps what a shape's __post_init__ refused in its own report, with the URL and the
# input; the sentence a person reads is the shape's, so it is read back out of the wrapper.
def _the_shapes_own_words(invalid: ValidationError) -> str:
    """The refusal a shape raised, when one did; pydantic's report otherwise."""
    for error in invalid.errors():
        cause = error.get("ctx", {}).get("error")
        if isinstance(cause, DeclarationRefused):
            return str(cause)
    return str(invalid)


def wire_tuning_row(kept: Kept[Tuning]) -> TuningRow:
    """One kept version as the doors answer it."""
    return TuningRow(
        holder=kept.holder,
        version=kept.version,
        author=kept.author,
        note=kept.note,
        set_at=kept.set_at.timestamp(),
        config=TuningBody.model_validate(tuning_json(kept.value)),
    )


def wire_lexicon_row(kept: Kept[Lexicon]) -> LexiconRow:
    """One kept lexicon version as the doors answer it."""
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


# ── whose corner, and the rules every set passes ────────────────────────────────


# The org's own corner for the team, for a key that names nobody, and for a key that holds no
# agent — a supervisor's, a manager's: no session ever resolves in their corner, so a set there
# would be a set nobody hears. Else the person's own, or the colleague's an admin is looking at.
def corner_written(key: KeyRecord, team: bool) -> str:
    """The corner a set lands in, as the column spells it."""
    if team or HOLDING not in key.scopes:
        return THE_ORGS_OWN
    return whose(is_held_by(key))


# A set needs no socket: a supervisor fixing tonight's opening has no app running, and the rules a
# tuning is checked against hold over the runtime's own defaults just the same. When an app IS
# holding the agent, its declaration is what the knobs are laid over, so a vendor is resolved
# against what the session would really run on.
def declared_or_bare(slug: str, key: KeyRecord, registry: Registry) -> AgentConfig:
    """What the app declared when one holds the agent; the bare slug when none does."""
    held = registry.of(key.env, slug, is_held_by(key))
    if held is not None and held.org != key.org:
        raise HTTPException(404, NO_AGENT.format(slug=slug))
    if held is not None:
        return held.config
    return AgentConfig(slug=slug)


def check_config(declared: AgentConfig, wanted: Tuning, lexicon: Lexicon) -> AgentConfig:
    """Building the config IS the check: 400 in the rule's own sentence when one breaks."""
    return apply_tuning(declared, wanted, lexicon)


def words_only(key: KeyRecord, wanted: Tuning, standing: Tuning) -> Tuning:
    """A words key's set with the pipeline carried over from what stands; 403 when it moved one."""
    carried: dict[str, Any] = {}
    touched: list[str] = []
    for name in PIPELINE_ONLY:
        sent, kept = getattr(wanted, name), getattr(standing, name)
        if sent is None:
            carried[name] = kept
        elif sent != kept:
            touched.append(name)
    turned = wanted.greeting
    if (
        turned is not None
        and turned != standing.greeting
        and (turned.reply is not None or turned.allow_interruptions is not None)
    ):
        touched.append("greeting.reply")
    if touched:
        refusal = cannot_open(key, "pipeline") or ""
        raise HTTPException(403, NOT_WORDS.format(fields=", ".join(touched), refusal=refusal))
    return dataclasses.replace(wanted, **carried)


def differing_fields(ours: Kept[Tuning] | None, theirs: Kept[Tuning] | None) -> list[str]:
    """The fields set differently between two versions, by name; every set one when one is None."""
    mine = {} if ours is None else tuning_json(ours.value)
    yours = {} if theirs is None else tuning_json(theirs.value)
    return sorted(name for name in set(mine) | set(yours) if mine.get(name) != yours.get(name))


# ── the doors ───────────────────────────────────────────────────────────────────


@router.get("/v1/agents/{slug}/settings")
async def settings(slug: str, key: TuningKeyDep, kept: TuningDep) -> TuningAnswer:
    """The agent's tuning as this key sees it: yours, the team's, production's."""
    corners = await settings_corners(kept, key.org, key.env, own_corner(key), slug)
    return wire_tuning_corners(corners, key.env)


# The whole set, every time, with the version it was read at: two people saving from two screens
# never write over each other in silence — the second is told where the corner is now, and reads
# it again. The GET answers each corner's OWN newest and not the fallback, because if_version is
# about the corner being written: a CLI sending the team's 3 into an empty corner of its own would
# be refused for a version that was never there.
@router.put("/v1/agents/{slug}/settings")
async def set_settings(
    slug: str,
    said: TuningPut,
    key: TuningKeyDep,
    registry: RegistryDep,
    kept: TuningDep,
    vault: VaultDep,
    orgs: OrgsDep,
) -> TuningAnswer:
    """Set this agent's tuning in this key's corner, or the team's: a new version, checked first."""
    corner = corner_written(key, said.team)
    wanted = parse_tuning(said.config)
    if "pipeline" not in key.scopes:
        standing = await kept.newest(key.org, key.env, corner, slug)
        wanted = words_only(key, wanted, Tuning() if standing is None else standing.value)
    words = await kept.newest_lexicon(key.org, key.env, corner)
    config = check_config(
        declared_or_bare(slug, key, registry), wanted, Lexicon() if words is None else words.value
    )
    # What the box does not lend this org is refused here, when it is picked, in the sentence the
    # call would refuse with — not saved to be refused on the next call.
    unlent = first_unlent_vendor(config, await brought_by(vault, orgs.quotas_of, key.org))
    if unlent is not None:
        raise HTTPException(422, unlent)
    await kept.put(
        key.org,
        key.env,
        corner,
        slug,
        wanted,
        author=author_of(key),
        note=said.note,
        if_version=said.if_version,
    )
    corners = await settings_corners(kept, key.org, key.env, own_corner(key), slug)
    return wire_tuning_corners(corners, key.env)


@router.get("/v1/agents/{slug}/settings/history")
async def history(
    slug: str,
    key: TuningKeyDep,
    kept: TuningDep,
    team: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = HISTORY_LIMIT,
) -> TuningHistory:
    """Every version this corner kept, newest first, each with who set it and why."""
    corner = corner_written(key, team)
    rows = await kept.history(key.org, key.env, corner, slug, limit)
    return TuningHistory(world=key.env, holder=corner, rows=[wire_tuning_row(row) for row in rows])


@router.get("/v1/agents/{slug}/settings/diff")
async def diff(
    slug: str,
    key: TuningKeyDep,
    kept: TuningDep,
    against: Annotated[Literal["team", "production"], Query()] = "production",
) -> TuningDiff:
    """What this key's corner reads, against the team's or production's newest."""
    ours = await kept.newest(key.org, key.env, is_held_by(key), slug)
    world = PRODUCTION if against == "production" else key.env
    theirs = await kept.own(key.org, world, THE_ORGS_OWN, slug)
    return TuningDiff(
        ours=None if ours is None else wire_tuning_row(ours),
        theirs=None if theirs is None else wire_tuning_row(theirs),
        changed=differing_fields(ours, theirs),
    )


# A rollback is a new version equal to an old one: nothing is deleted, and the history says a
# person went back and to what. It works in production as a set does: the row it copies was set
# there once, by somebody the org let act there.
@router.post("/v1/agents/{slug}/settings/rollback")
async def rollback(slug: str, said: Rollback, key: PipelineKeyDep, kept: TuningDep) -> TuningAnswer:
    """Bring one version back, as the next version of this corner."""
    corner = corner_written(key, said.team)
    row = await kept.at(key.org, key.env, corner, slug, said.version)
    if row is None:
        raise HTTPException(404, NO_SUCH_VERSION.format(version=said.version, slug=slug))
    await kept.put(
        key.org,
        key.env,
        corner,
        slug,
        row.value,
        author=author_of(key),
        note=f"rollback to v{said.version}",
        if_version=None,
    )
    corners = await settings_corners(kept, key.org, key.env, own_corner(key), slug)
    return wire_tuning_corners(corners, key.env)


# What a call ran on, exactly: the head row kept the two version numbers when the call opened
# (api/calls/events.py), and this reads the rows back by them. A reviewer of Tuesday's bad call
# sees Tuesday's voice, model and words, whatever changed since.
@router.get("/v1/calls/{call}/settings")
async def tuning_of_call(
    call: str, key: CallsKeyDep, index: CallIndexDep, kept: TuningDep
) -> CallTuning:
    """The tuning and the lexicon this call was built on, by the versions its head row recorded."""
    corner = await index.corner_of_call(call)
    if corner is None or corner.org is None or corner.org != key.org:
        raise HTTPException(404, NO_SUCH_CALL.format(call=call))
    org, env = corner.org, parse_env(corner.env)
    config = (
        None
        if corner.config_version is None
        else await kept.at(org, env, corner.holder, corner.agent, corner.config_version)
    )
    words = (
        None
        if corner.lexicon_version is None
        else await kept.lexicon_at(org, env, corner.holder, corner.lexicon_version)
    )
    return CallTuning(
        config_version=corner.config_version,
        lexicon_version=corner.lexicon_version,
        config=None if config is None else wire_tuning_row(config),
        lexicon=None if words is None else wire_lexicon_row(words),
    )


def wire_tuning_corners(corners: Corners[Tuning], world: Env) -> TuningAnswer:
    """The three corners as the settings doors answer them."""
    return TuningAnswer(
        world=world,
        yours=None if corners.yours is None else wire_tuning_row(corners.yours),
        team=None if corners.team is None else wire_tuning_row(corners.team),
        production=None if corners.production is None else wire_tuning_row(corners.production),
    )


def own_corner(key: KeyRecord) -> str | None:
    """The corner that is this key's own, or None for a key that holds no agent."""
    return is_held_by(key) if HOLDING in key.scopes else None
