"""A push's whole files are weighed: past the heavy line the answer says so, and the push lands."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.knowledge import HEAVY
from pinecall.types.counting import TOKENS_PER_WORD, estimated_tokens
from pinecall.types.knowledge import HEAVY_WHOLE_TOKENS
from tests.lookups.fake_knowledge import ScriptedKnowledge

pytestmark = pytest.mark.unit

PUSH = "/v1/knowledge/clinica"
# Past the line by a few words, whatever the line is.
HEAVY_TEXT = "palabra " * (round(HEAVY_WHOLE_TOKENS / TOKENS_PER_WORD) + 10)


@pytest.fixture
def knowledge() -> ScriptedKnowledge:
    """A base nobody has pushed to yet."""
    return ScriptedKnowledge()


def a_push(*files: tuple[str, str, str]) -> dict[str, object]:
    """The body of a push: each file's path, text and mode."""
    return {"files": [{"path": path, "text": text, "mode": mode} for path, text, mode in files]}


async def test_a_heavy_whole_file_lands_and_the_answer_says_what_it_weighs(
    tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    pushed = await tenant_http.put(PUSH, json=a_push(("clinica.md", HEAVY_TEXT, "whole")))
    assert pushed.status_code == 200
    body = pushed.json()
    tokens = estimated_tokens(HEAVY_TEXT)
    assert body["whole_tokens"] == tokens
    assert body["notice"] == HEAVY.format(
        base="clinica", tokens=f"{tokens:,}", heavy=f"{HEAVY_WHOLE_TOKENS:,}"
    )
    assert [file.path for file in knowledge.pushed["clinica"]] == ["clinica.md"]


async def test_a_light_whole_file_is_weighed_and_says_nothing(
    tenant_http: httpx.AsyncClient,
) -> None:
    body = (
        await tenant_http.put(PUSH, json=a_push(("clinica.md", "Abrimos a las nueve.", "whole")))
    ).json()
    assert body["whole_tokens"] == estimated_tokens("Abrimos a las nueve.")
    assert body["notice"] is None


async def test_only_the_whole_files_weigh_never_the_ones_a_turn_searches(
    tenant_http: httpx.AsyncClient,
) -> None:
    body = (
        await tenant_http.put(PUSH, json=a_push(("docs/todo.md", HEAVY_TEXT, "retrieved")))
    ).json()
    assert (body["whole_tokens"], body["notice"]) == (0, None)
