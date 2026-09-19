"""The org's lexicon doors: how every agent says its words and what the ears know, versioned."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from pinecall.api._deps import TuningDep
from pinecall.api.tuning import TuningKeyDep, a_lexicon_row, corner_written, refuse_in_production
from pinecall.auth.corner import author_of
from pinecall.auth.keys import KeyRecord, held_by
from pinecall.orgs.tuning import HISTORY_LIMIT, TuningStore, VersionMoved
from pinecall.types import HOLDING, PRODUCTION, THE_ORGS_OWN, DeclarationRefused, Lexicon
from pinecall_protocol.rest import LexiconAnswer, LexiconBody, LexiconHistory, LexiconPut

router = APIRouter()

PROMOTE_LEXICON = "POST /v1/lexicon/promote with to=production"


def a_lexicon(body: LexiconBody) -> Lexicon:
    """The wire's body as the domain's shape, or 400 in the shape's own sentence."""
    try:
        return Lexicon(said={one.word: one.spoken for one in body.said}, heard=tuple(body.heard))
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused


@router.get("/v1/lexicon")
async def lexicon(key: TuningKeyDep, kept: TuningDep) -> LexiconAnswer:
    """The org's words as this key sees them: yours, the team's, production's."""
    return await _answer(key, kept)


# The words are the org's, and the person who hears one said wrong is the one who fixes it: a
# supervisor's key opens this door. Whole set, with the version it was read at, as the settings.
@router.put("/v1/lexicon")
async def set_lexicon(said: LexiconPut, key: TuningKeyDep, kept: TuningDep) -> LexiconAnswer:
    """Set the lexicon in this key's corner, or the team's: a new version."""
    refuse_in_production(key, PROMOTE_LEXICON)
    corner = corner_written(key, said.team)
    wanted = a_lexicon(said.lexicon)
    try:
        await kept.put_lexicon(
            key.org,
            key.env,
            corner,
            wanted,
            author=author_of(key),
            note=said.note,
            if_version=said.if_version,
        )
    except VersionMoved as moved:
        raise HTTPException(409, str(moved)) from moved
    return await _answer(key, kept)


@router.get("/v1/lexicon/history")
async def history(
    key: TuningKeyDep,
    kept: TuningDep,
    team: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = HISTORY_LIMIT,
) -> LexiconHistory:
    """Every version this corner kept, newest first."""
    corner = corner_written(key, team)
    rows = await kept.lexicon_history(key.org, key.env, corner, limit)
    return LexiconHistory(world=key.env, holder=corner, rows=[a_lexicon_row(row) for row in rows])


async def _answer(key: KeyRecord, kept: TuningStore) -> LexiconAnswer:
    """The three corners as this key sees them, each its own newest."""
    mine = held_by(key) if HOLDING in key.scopes else None
    yours = None if mine is None else await kept.own_lexicon(key.org, key.env, mine)
    team = await kept.own_lexicon(key.org, key.env, THE_ORGS_OWN)
    production = await kept.own_lexicon(key.org, PRODUCTION, THE_ORGS_OWN)
    return LexiconAnswer(
        world=key.env,
        yours=None if yours is None else a_lexicon_row(yours),
        team=None if team is None else a_lexicon_row(team),
        production=None if production is None else a_lexicon_row(production),
    )
