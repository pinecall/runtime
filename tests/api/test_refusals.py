"""A door that cannot embed says so: the vendor, the URL and the reason, never a bare 500."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from pinecall import api
from pinecall.api.calls.supervise.aiming import VerbRefused
from pinecall.api.evals.runner import AlreadyRunning, NobodyServing
from pinecall.api.refusals import STATUS_OF, WITH_THEIR_OWN_STATUS, refusals_answered_by
from pinecall.auth.identity import NotRedeemed
from pinecall.log.filters import FilterRefused
from pinecall.orgs.admission import Exhausted, QuotaExhausted
from pinecall.orgs.caller_codes import TooManyCodes
from pinecall.orgs.outbound_guards import STATUS, STRANGER, TOO_FAST, DialRefused, Refusal
from pinecall.orgs.personas import NameTaken, NoSuchPersona
from pinecall.orgs.tuning_store import VersionMoved
from pinecall.providers.embedder import EmbedderUnreachable, WrongModel, WrongWidth
from pinecall.providers.registry import NoProvider
from pinecall.providers.tts.vendor_voices import NotListed
from pinecall.routes.twilio import TwilioRefused
from pinecall.session.hold_melody import NotAHoldMelody
from pinecall.types import DeclarationRefused
from tests.lookups.fakes import ScriptedKnowledge, ScriptedMemory

pytestmark = pytest.mark.unit

KNOWLEDGE = "/v1/knowledge/clinica"
A_PUSH = {"files": [{"path": "tarifas.md", "text": "# Tarifas\n\nRevisión: 45 €."}]}
CONTACT = "/v1/contacts/c_1/memory"

# The sentence a laptop with no TEI actually reads, word for word.
TEI_IS_DOWN = EmbedderUnreachable(
    "TEI at http://127.0.0.1:8081 did not answer: All connection attempts failed"
)
TOO_NARROW = WrongWidth(
    "all-MiniLM-L6-v2 answers 384-wide vectors; the tables are declared at 1024, "
    "the width every halfvec column holds"
)
ANOTHER_MODEL = WrongModel(
    "base clinica-norte was pushed with pplx-embed-context-v1-0.6b; "
    "this gateway embeds with BAAI/bge-m3: push it again"
)


@pytest.fixture
def knowledge() -> ScriptedKnowledge:
    """The knowledge base this gateway holds; a test hands it the refusal to raise."""
    return ScriptedKnowledge()


@pytest.fixture
def memory() -> ScriptedMemory:
    """The memory this gateway holds; a test hands it the refusal to raise."""
    return ScriptedMemory()


async def test_a_push_with_no_embedder_answers_503_with_the_vendor_the_url_and_the_reason(
    tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    """What `pinecall knowledge push` prints on a Mac, where TEI has no image to run at all."""
    knowledge.failing = TEI_IS_DOWN
    refused = await tenant_http.put(KNOWLEDGE, json=A_PUSH)
    assert refused.status_code == 503
    assert refused.json()["detail"] == str(TEI_IS_DOWN)
    assert "Internal Server Error" not in refused.text


async def test_a_push_whose_embedder_answers_another_width_is_409_naming_the_model(
    tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    knowledge.failing = TOO_NARROW
    refused = await tenant_http.put(KNOWLEDGE, json=A_PUSH)
    assert refused.status_code == 409
    assert refused.json()["detail"] == str(TOO_NARROW)


async def test_a_base_of_another_model_is_409_naming_both_models_and_the_way_out(
    tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    knowledge.failing = ANOTHER_MODEL
    refused = await tenant_http.put(KNOWLEDGE, json=A_PUSH)
    assert refused.status_code == 409
    assert refused.json()["detail"] == str(ANOTHER_MODEL)
    assert "push it again" in refused.text


async def test_it_is_every_door_and_not_one_so_a_contacts_memory_says_the_same(
    tenant_http: httpx.AsyncClient, memory: ScriptedMemory
) -> None:
    """One table maps the refusal to the status (api/refusals.py); no endpoint writes a catch."""
    memory.failing = TEI_IS_DOWN
    refused = await tenant_http.get(CONTACT)
    assert refused.status_code == 503
    assert refused.json()["detail"] == str(TEI_IS_DOWN)


# ── The table itself: every refusal a door may let through, and the status it lands as ──────

TABLED: list[tuple[Exception, int]] = [
    (DeclarationRefused("a slug is [a-z0-9-]"), 400),
    (FilterRefused("too many types"), 400),
    (NotAHoldMelody("not an Ogg Opus file"), 400),
    (NoSuchPersona("no persona named ana"), 404),
    (NobodyServing("nobody holds clinica"), 404),
    (NotListed("no catalogue for acme"), 404),
    (VersionMoved(7), 409),
    (NameTaken("ana is held"), 409),
    (AlreadyRunning("run r_1 holds clinica"), 409),
    (QuotaExhausted(Exhausted(org="o_1", quota="agents", used=3, limit=3)), 429),
    (TooManyCodes("five codes are open"), 429),
    (TwilioRefused("Twilio said 20404"), 502),
    (NoProvider("acme has no API key in this process"), 503),
    (DialRefused(Refusal(guard=STRANGER, said="not on the list")), STATUS[STRANGER]),
    (DialRefused(Refusal(guard=TOO_FAST, said="one dial a minute")), STATUS[TOO_FAST]),
    (VerbRefused(409, "the call is not held"), 409),
    (NotRedeemed(410, "production: the code expired"), 410),
]


@pytest.mark.parametrize(
    "refusal, status",
    TABLED,
    ids=lambda x: type(x).__name__ if isinstance(x, Exception) else str(x),
)
async def test_every_tabled_refusal_lands_as_its_status_with_its_own_sentence(
    refusal: Exception, status: int
) -> None:
    """One table, one handler each, and never a catch in a door (api/refusals.py)."""
    gateway = FastAPI()
    refusals_answered_by(gateway)

    async def door() -> None:
        raise refusal

    gateway.add_api_route("/door", door)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://gateway"
    ) as http:
        answered = await http.get("/door")
    assert (answered.status_code, answered.json()) == (status, {"detail": str(refusal)})


def test_nothing_in_the_table_is_still_caught_by_hand_in_a_door() -> None:
    """The catch a door kept is one answering ANOTHER status, or another sentence, on purpose."""
    status_of = {refusal.__name__: status for refusal, status in STATUS_OF.items()}
    their_own = {refusal.__name__ for refusal in WITH_THEIR_OWN_STATUS}
    by_hand: list[str] = []
    for module in sorted(Path(api.__file__).parent.rglob("*.py")):
        lines = module.read_text().splitlines()
        for number, line in enumerate(lines):
            caught = re.match(r"\s*except (\w+) as (\w+):", line)
            if caught is None:
                continue
            refusal, name = caught.groups()
            following = " ".join(lines[number + 1 : number + 3])
            tabled = (
                refusal in status_of
                and f"HTTPException({status_of[refusal]}, str({name}))" in following
            ) or (refusal in their_own and f"HTTPException({name}.status" in following)
            if tabled:
                by_hand.append(f"{module.name}:{number + 1}")
    assert not by_hand, f"the table already answers these: {by_hand}"
