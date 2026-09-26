"""The org's lexicon doors: how every agent says its words and what the ears know, versioned."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from pinecall.api.agents.tuning import TuningKeyDep, corner_written, own_corner, wire_lexicon_row
from pinecall.api.deps import TuningDep
from pinecall.auth.request_scope import author_of
from pinecall.orgs import Corners, lexicon_corners
from pinecall.orgs.tuning_store import HISTORY_LIMIT
from pinecall.types import Env, Lexicon
from pinecall_protocol.rest import LexiconAnswer, LexiconBody, LexiconHistory, LexiconPut

router = APIRouter()


def parse_lexicon(body: LexiconBody) -> Lexicon:
    """The wire's body as the domain's shape, or 400 in the shape's own sentence."""
    return Lexicon(said={one.word: one.spoken for one in body.said}, heard=tuple(body.heard))


@router.get("/v1/lexicon")
async def lexicon(key: TuningKeyDep, kept: TuningDep) -> LexiconAnswer:
    """The org's words as this key sees them: yours, the team's, production's."""
    corners = await lexicon_corners(kept, key.org, key.env, own_corner(key))
    return wire_lexicon_corners(corners, key.env)


# The words are the org's, and the person who hears one said wrong is the one who fixes it: a
# supervisor's key opens this door. Whole set, with the version it was read at, as the settings.
@router.put("/v1/lexicon")
async def set_lexicon(said: LexiconPut, key: TuningKeyDep, kept: TuningDep) -> LexiconAnswer:
    """Set the lexicon in this key's corner, or the team's: a new version."""
    corner = corner_written(key, said.team)
    wanted = parse_lexicon(said.lexicon)
    await kept.put_lexicon(
        key.org,
        key.env,
        corner,
        wanted,
        author=author_of(key),
        note=said.note,
        if_version=said.if_version,
    )
    corners = await lexicon_corners(kept, key.org, key.env, own_corner(key))
    return wire_lexicon_corners(corners, key.env)


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
    return LexiconHistory(
        world=key.env, holder=corner, rows=[wire_lexicon_row(row) for row in rows]
    )


def wire_lexicon_corners(corners: Corners[Lexicon], world: Env) -> LexiconAnswer:
    """The three corners as the lexicon doors answer them."""
    return LexiconAnswer(
        world=world,
        yours=None if corners.yours is None else wire_lexicon_row(corners.yours),
        team=None if corners.team is None else wire_lexicon_row(corners.team),
        production=None if corners.production is None else wire_lexicon_row(corners.production),
    )
