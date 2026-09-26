"""`/v1/login/pairings`: the word `pinecall login` prints, and the key a browser leaves for it."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from starlette.status import HTTP_202_ACCEPTED

from pinecall.api.accounts.identity import AtProduction
from pinecall.api.accounts.login import WordMinted, for_the_same_person
from pinecall.api.deps import KeyDep, KeysDep, MembersDep, PairingsDep, SettingsDep
from pinecall_protocol import WireModel

# Production's alone (api/accounts/identity.py): a sandbox keeps no password and makes no person, so
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


class Opening(WireModel):
    """What the terminal says about itself, so the card names what is being signed in."""

    device: str | None = None


class PairingAsked(WireModel):
    """GET /v1/login/pairings/{code}: which terminal, when the word dies, whether it is answered."""

    device: str | None
    expires_at: float
    answered: bool


class PairingAnswered(WireModel):
    """POST /v1/login/pairings/{code}: which terminal was signed in, and into which org."""

    device: str | None
    org: str


class KeyCollected(WireModel):
    """GET /v1/login/pairings/{code}/key, 200: the key the browser left, once."""

    key: str


class StillWaiting(WireModel):
    """GET /v1/login/pairings/{code}/key, 202: nothing yet — the browser has not answered."""


@router.post("/v1/login/pairings")
async def a_pairing(said: Opening, pairings: PairingsDep) -> WordMinted:
    """A word for a terminal to print, and when it dies. No key: the terminal has none yet."""
    opened = pairings.open(said.device)
    return WordMinted(code=opened.code, expires_at=opened.expires_at)


@router.get("/v1/login/pairings/{code}")
async def asking(code: str, pairings: PairingsDep) -> PairingAsked:
    """What the card is about to approve. It spends nothing, and no key is ever in it."""
    asked = pairings.asking(code)
    if asked is None:
        raise HTTPException(404, NO_PAIRING)
    return PairingAsked(device=asked.device, expires_at=asked.expires_at, answered=asked.answered)


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
) -> PairingAnswered:
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
    if not pairings.fill(code, issued.key, key.org):
        raise HTTPException(409, ANSWERED)
    return PairingAnswered(device=asked.device, org=key.org)


@router.get("/v1/login/pairings/{code}/key")
async def collected(
    code: str, response: Response, pairings: PairingsDep
) -> KeyCollected | StillWaiting:
    """The key the browser left, once: this is the terminal's door and it spends the word."""
    found = pairings.collect(code)
    if found.key is not None:
        return KeyCollected(key=found.key)
    if found.waiting:
        # 202: the browser has not answered yet and the terminal should ask again. Not 404, which
        # is what a word that is gone answers, and not an empty 200, which a terminal would read
        # as a key of "".
        response.status_code = HTTP_202_ACCEPTED
        return StillWaiting()
    raise HTTPException(404, NO_PAIRING)
