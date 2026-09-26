"""The org's synthetic callers: listed, written and dropped, as the world's and not the code's."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from starlette.requests import HTTPConnection

from pinecall.api._deps import EvalsKeyDep, held
from pinecall.auth.corner import author_of
from pinecall.orgs.personas import Personas
from pinecall.providers.tuning import the_llm, the_voice
from pinecall.types import DeclarationRefused
from pinecall_protocol.rest import Persona, PersonaList, PersonaPut

router = APIRouter()


def the_personas(connection: HTTPConnection) -> Personas:
    """Where this org's callers are kept. The doors that ask for them are this file and its runs."""
    return held(connection, "personas")


PersonasDep = Annotated[Personas, Depends(the_personas)]

# A caller is named as `--persona` takes it, and as its file was: lower-case words and hyphens.
A_NAME = "abcdefghijklmnopqrstuvwxyz0123456789-"
NOT_A_NAME = (
    "{name!r} is not a persona's name: lower-case words joined by hyphens, like price-shopper"
)


# One list an ORG, and no agent in the path: a caller is a person on the phone, and who they are
# does not depend on which of the org's agents picks up. It was `/v1/agents/{slug}/personas`, which
# made a team write `price-shopper` once for sales and again for dispatch, and made the console
# group one list by something that is not a property of it.
@router.get("/v1/personas")
async def personas(key: EvalsKeyDep, kept: PersonasDep) -> PersonaList:
    """Every caller this org wrote. One list an org, whichever agent and whichever world asks."""
    return PersonaList(personas=[Persona(**one) for one in await kept.of(key.org)])


# Written by whoever runs the evals: the caller is a test, so `evals` opens it — the same scope
# that runs a simulation with one. There is no corner and no version: a persona is not something
# a customer hears, and two people writing one are two people writing a test.
@router.put("/v1/personas/{name}")
async def put_persona(
    name: str, said: PersonaPut, key: EvalsKeyDep, kept: PersonasDep
) -> PersonaList:
    """The caller written whole — new, replaced, or renamed from `was` — and the list after."""
    _a_name(name)
    llm, tts, voice = _played_as(said)
    written = await kept.put(
        key.org,
        name,
        about=said.about or "",
        goal=said.goal,
        style=said.style,
        facts=said.facts or {},
        state=said.state or {},
        author=author_of(key),
        was=said.was,
        llm=llm,
        tts=tts,
        voice=voice,
        accepts_when=said.accepts_when or "",
        declines_when=said.declines_when or "",
    )
    return PersonaList(personas=[Persona(**one) for one in written])


@router.delete("/v1/personas/{name}")
async def drop_persona(name: str, key: EvalsKeyDep, kept: PersonasDep) -> PersonaList:
    """The caller's row gone, and the list after. A name nobody wrote is a 404 that says so."""
    return PersonaList(personas=[Persona(**one) for one in await kept.drop(key.org, name)])


# The three knobs are the agent's own — `pinecall agent set --llm`, `--tts`, `--voice` — read by
# the same parser, and refused HERE for the same typos: a vendor this build has no file for, a
# voice name nobody curated. The alternative was `carolinaa` reaching the vendor on the caller's
# first line and the run dying with a 1008 instead of the page saying so on save. Empty is unset.
def _played_as(said: PersonaPut) -> tuple[str | None, str | None, str | None]:
    """The model, the vendor and the voice as the table keeps them, or 422 in the parser's words."""
    llm, tts, voice = said.llm or None, said.tts or None, said.voice or None
    try:
        the_llm(llm)
        the_voice(tts, voice)
    except DeclarationRefused as refused:
        raise HTTPException(422, str(refused)) from refused
    return llm, tts, voice


def _a_name(name: str) -> None:
    """A name a file could have carried, or 422 in the sentence the CLI and the page both say."""
    parts = name.split("-")
    if (
        name == ""
        or any(part == "" for part in parts)
        or any(letter not in A_NAME for letter in name)
    ):
        raise HTTPException(422, NOT_A_NAME.format(name=name))
