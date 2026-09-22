"""The org's personas over the wire: listed, written, renamed, dropped — and the names refused."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from pinecall.api.app import app
from pinecall.api.personas import the_personas
from pinecall.orgs.personas import MemoryPersonas

pytestmark = pytest.mark.unit

PERSONAS = "/v1/personas"


# This door's own store, for the length of one test: `wired` answers every other dependency, and
# a table only this page reads is overridden where it is read.
@pytest.fixture(autouse=True)
def personas() -> Iterator[MemoryPersonas]:
    """The org's callers, empty at the start of every test."""
    kept = MemoryPersonas()
    app.dependency_overrides[the_personas] = lambda: kept
    yield kept
    app.dependency_overrides.pop(the_personas, None)


DANA = {
    "about": "A homeowner moving out.",
    "goal": "get a move-out cleaning quote",
    "style": "friendly, a little rushed",
    "facts": {"their name": "Dana Ruiz"},
}


async def test_an_org_with_nobody_written_for_it_lists_none(
    tenant_http: httpx.AsyncClient,
) -> None:
    listed = await tenant_http.get(PERSONAS)

    assert listed.status_code == 200
    assert listed.json() == {"personas": []}


async def test_one_written_comes_back_whole_with_who_wrote_it(
    tenant_http: httpx.AsyncClient,
) -> None:
    written = await tenant_http.put(f"{PERSONAS}/homeowner", json=DANA)

    assert written.status_code == 200
    [one] = written.json()["personas"]
    assert (one["name"], one["goal"], one["facts"]) == ("homeowner", DANA["goal"], DANA["facts"])
    assert one["state"] == {}
    assert one["author"] != ""
    assert one["set_at"] > 0


async def test_writing_the_same_name_again_replaces_it(tenant_http: httpx.AsyncClient) -> None:
    await tenant_http.put(f"{PERSONAS}/homeowner", json=DANA)

    again = await tenant_http.put(
        f"{PERSONAS}/homeowner", json={**DANA, "goal": "get a price today"}
    )

    assert [one["goal"] for one in again.json()["personas"]] == ["get a price today"]


async def test_a_state_travels_whole_for_a_caller_the_business_knows(
    tenant_http: httpx.AsyncClient,
) -> None:
    written = await tenant_http.put(
        f"{PERSONAS}/known", json={**DANA, "state": {"stage": "book", "patient": {"id": "p-1"}}}
    )

    [one] = written.json()["personas"]
    assert one["state"] == {"stage": "book", "patient": {"id": "p-1"}}


async def test_a_rename_takes_the_old_row_with_it(tenant_http: httpx.AsyncClient) -> None:
    await tenant_http.put(f"{PERSONAS}/homeowner", json=DANA)

    renamed = await tenant_http.put(f"{PERSONAS}/dana", json={**DANA, "was": "homeowner"})

    assert [one["name"] for one in renamed.json()["personas"]] == ["dana"]


async def test_a_rename_onto_a_name_somebody_holds_is_refused(
    tenant_http: httpx.AsyncClient,
) -> None:
    await tenant_http.put(f"{PERSONAS}/homeowner", json=DANA)
    await tenant_http.put(f"{PERSONAS}/dana", json=DANA)

    clash = await tenant_http.put(f"{PERSONAS}/dana", json={**DANA, "was": "homeowner"})

    assert clash.status_code == 409
    assert "already" in clash.json()["detail"]


async def test_a_rename_of_a_caller_nobody_wrote_is_a_404(tenant_http: httpx.AsyncClient) -> None:
    nobody = await tenant_http.put(f"{PERSONAS}/dana", json={**DANA, "was": "nobody"})

    assert nobody.status_code == 404


# The name is the file's name that was, and `--persona` takes it: a space or a capital would be a
# caller nobody could call with.
@pytest.mark.parametrize("name", ["Dana Ruiz", "dana_ruiz", "dana--ruiz", "-dana"])
async def test_a_name_that_is_not_a_name_is_refused(
    tenant_http: httpx.AsyncClient, name: str
) -> None:
    refused = await tenant_http.put(f"{PERSONAS}/{name}", json=DANA)

    assert refused.status_code == 422


async def test_a_caller_that_says_how_it_is_played_and_when_it_accepts_comes_back_saying_it(
    tenant_http: httpx.AsyncClient,
) -> None:
    played = {
        "llm": "anthropic/claude-haiku-4-5",
        "tts": "elevenlabs",
        "voice": "carolina",
        "accepts_when": "they gave a price for Friday",
        "declines_when": "they asked to be called back",
    }

    [one] = (await tenant_http.put(f"{PERSONAS}/homeowner", json={**DANA, **played})).json()[
        "personas"
    ]

    assert {field: one[field] for field in played} == played


async def test_a_caller_that_says_nothing_about_it_is_played_as_every_caller_is(
    tenant_http: httpx.AsyncClient,
) -> None:
    [one] = (await tenant_http.put(f"{PERSONAS}/homeowner", json=DANA)).json()["personas"]

    assert (one["llm"], one["tts"], one["voice"]) == (None, None, None)
    assert (one["accepts_when"], one["declines_when"]) == ("", "")


# The agent's own knobs, refused for the agent's own typos: the page says so on save, rather than
# the caller's first line dying at the vendor with a 1008 halfway through a run.
@pytest.mark.parametrize(
    ("knob", "said"),
    [
        ({"llm": "openai-but-misspelt/gpt-5"}, "no llm vendor named"),
        ({"tts": "elevenlabz/eleven_v3"}, "no tts vendor named"),
        ({"voice": "carolinaa"}, "no voice named 'carolinaa'"),
    ],
)
async def test_a_model_or_a_voice_this_build_does_not_have_is_refused_when_it_is_written(
    tenant_http: httpx.AsyncClient, knob: dict[str, str], said: str
) -> None:
    refused = await tenant_http.put(f"{PERSONAS}/homeowner", json={**DANA, **knob})

    assert refused.status_code == 422
    assert said in refused.json()["detail"]
    assert (await tenant_http.get(PERSONAS)).json() == {"personas": []}


async def test_one_dropped_is_gone_and_a_name_nobody_wrote_is_a_404(
    tenant_http: httpx.AsyncClient,
) -> None:
    await tenant_http.put(f"{PERSONAS}/homeowner", json=DANA)

    assert (await tenant_http.delete(f"{PERSONAS}/homeowner")).json() == {"personas": []}
    assert (await tenant_http.delete(f"{PERSONAS}/homeowner")).status_code == 404


# One list an ORG: a caller is a person on the phone, and who they are does not depend on which
# of the org's agents answers. The world the key acts in does not cut them apart either — a caller
# is a test, not something a customer hears.
async def test_one_caller_written_once_is_the_whole_orgs(
    tenant_http: httpx.AsyncClient, personas: MemoryPersonas
) -> None:
    await tenant_http.put(f"{PERSONAS}/homeowner", json=DANA)

    listed = await tenant_http.get(PERSONAS)

    assert [one["name"] for one in listed.json()["personas"]] == ["homeowner"]
    assert [one["name"] for one in await personas.of("clinica")] == ["homeowner"]
