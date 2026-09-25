"""`/v1/login/pairings`: the word `pinecall login` prints, and the key a browser leaves for it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response

from pinecall.api._deps import KeyDep, KeysDep, MembersDep, PairingsDep, SettingsDep
from pinecall.api.identity import AtProduction
from pinecall.api.login import for_the_same_person
from pinecall_protocol import WireModel

# Production's alone (api/identity.py): a sandbox keeps no password and makes no person, so
# there every door here is 404, naming where people sign in.
router = APIRouter(dependencies=[AtProduction])

# The same answer for a word unknown, expired, or already collected: from the asking side there
# is nothing there, and telling them apart would say which words have existed.
NO_PAIRING = "no pairing answers to that: it was collected, it expired, or it never existed"

# Somebody opened the card twice, or two people did. The key is already waiting for the terminal
# that asked, and a second one would be a key nobody collects.
ANSWERED = "that terminal is already signed in: it has a key waiting"

# A terminal is signed in as a PERSON. An org's own key names nobody, so there is nobody to be.
NOT_A_PERSON = "an org's own key names nobody: a terminal is signed in as a person"

# 202: the browser has not answered yet and the terminal should ask again. Not 404, which is what
# a word that is gone answers, and not an empty 200, which a terminal would read as a key of "".
WAITING = 202


class Opening(WireModel):
    """What the terminal says about itself, so the card names what is being signed in."""

    device: str | None = None


@router.post("/v1/login/pairings")
async def a_pairing(said: Opening, pairings: PairingsDep) -> dict[str, Any]:
    """A word for a terminal to print, and when it dies. No key: the terminal has none yet."""
    opened = pairings.open(said.device)
    return {"code": opened.code, "expires_at": opened.expires_at}


@router.get("/v1/login/pairings/{code}")
async def asking(code: str, pairings: PairingsDep) -> dict[str, Any]:
    """What the card is about to approve. It spends nothing, and no key is ever in it."""
    asked = pairings.asking(code)
    if asked is None:
        raise HTTPException(404, NO_PAIRING)
    return {"device": asked.device, "expires_at": asked.expires_at, "answered": asked.answered}


# The browser holds a person's key and the terminal holds none, which is the whole point: the
# password is typed into a page, never into a terminal, so the day an org signs in with Google
# this door does not change. The key left here is the terminal's OWN — minted fresh for the same
# person, revoked on its own from the Keys screen — and never the browser's.
@router.post("/v1/login/pairings/{code}")
async def approve(
    code: str,
    key: KeyDep,
    keys: KeysDep,
    members: MembersDep,
    pairings: PairingsDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Sign that terminal in as the person this browser is: a sandbox key of its own."""
    if key.subject is None:
        raise HTTPException(403, NOT_A_PERSON)
    asked = pairings.asking(code)
    if asked is None:
        raise HTTPException(404, NO_PAIRING)
    if asked.answered:
        raise HTTPException(409, ANSWERED)
    # The terminal's key is the person's own, as every key of theirs: what it opens in production
    # is what their row says (auth/world.py), and a request names no world unless `--prod` says
    # so. docs/worlds-and-teams.md.
    issued = await for_the_same_person(key, asked.device, keys, members, settings.world)
    if not pairings.fill(code, str(issued["key"]), key.org):
        raise HTTPException(409, ANSWERED)
    return {"device": asked.device, "org": key.org}


@router.get("/v1/login/pairings/{code}/key")
async def collected(code: str, response: Response, pairings: PairingsDep) -> dict[str, Any]:
    """The key the browser left, once: this is the terminal's door and it spends the word."""
    found = pairings.collect(code)
    if found.key is not None:
        return {"key": found.key}
    if found.waiting:
        response.status_code = WAITING
        return {}
    raise HTTPException(404, NO_PAIRING)
