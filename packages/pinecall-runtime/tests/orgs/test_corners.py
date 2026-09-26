"""A key reads three corners, each its own; a session reads one, falling through to the org's."""

import pytest

from pinecall.orgs.corners import lexicon_corners, settings_corners, standing_in, tuned_for
from pinecall.orgs.tuning_store_memory import MemoryTuning
from pinecall.types import PRODUCTION, SANDBOX, THE_ORGS_OWN, AgentConfig, Lexicon, Tuning

pytestmark = pytest.mark.unit

ORG = "clinica"
AGENT = "clinica-norte"
ANA = "m_ana"
THE_TEAMS = Tuning(llm="anthropic/claude-sonnet-4-5", stt="soniox")
A_VOICE = Tuning(voice="carolina")
PRODUCTIONS = Tuning(llm="anthropic/claude-haiku-4-5")
WORDS = Lexicon(said={"ibuprofeno": "ibu-pro-FE-no"}, heard=("Reumatología",))


async def a_store() -> MemoryTuning:
    kept = MemoryTuning()
    await kept.put(
        ORG, SANDBOX, THE_ORGS_OWN, AGENT, THE_TEAMS, author=ANA, note=None, if_version=None
    )
    await kept.put(ORG, SANDBOX, ANA, AGENT, A_VOICE, author=ANA, note=None, if_version=None)
    await kept.put(
        ORG, PRODUCTION, THE_ORGS_OWN, AGENT, PRODUCTIONS, author=ANA, note=None, if_version=None
    )
    await kept.put_lexicon(
        ORG, SANDBOX, THE_ORGS_OWN, WORDS, author=ANA, note=None, if_version=None
    )
    return kept


async def test_each_corner_is_its_own_newest_and_never_the_fallback() -> None:
    kept = await a_store()
    corners = await settings_corners(kept, ORG, SANDBOX, ANA, AGENT)
    assert corners.yours is not None and corners.yours.value == A_VOICE
    assert corners.team is not None and corners.team.value == THE_TEAMS
    assert corners.production is not None and corners.production.value == PRODUCTIONS
    words = await lexicon_corners(kept, ORG, SANDBOX, ANA)
    assert words.yours is None
    assert words.team is not None and words.team.value == WORDS


async def test_a_key_holding_no_agent_has_no_corner_of_its_own() -> None:
    kept = await a_store()
    corners = await settings_corners(kept, ORG, SANDBOX, None, AGENT)
    assert corners.yours is None and corners.team is not None


async def test_what_stands_in_a_corner_falls_through_knob_by_knob_and_names_its_versions() -> None:
    kept = await a_store()
    standing = await standing_in(kept, ORG, SANDBOX, ANA, AGENT)
    assert standing.tuning == Tuning(llm=THE_TEAMS.llm, stt="soniox", voice="carolina")
    assert standing.lexicon == WORDS
    assert (standing.versions.config, standing.versions.lexicon) == (1, 1)


async def test_a_session_is_the_declaration_under_what_stands() -> None:
    kept = await a_store()
    tuned = await tuned_for(kept, ORG, SANDBOX, ANA, AGENT, AgentConfig(slug=AGENT))
    llm = tuned.config.llm
    assert llm is not None and (llm.provider, llm.model) == ("anthropic", "claude-sonnet-4-5")
    assert (tuned.versions.config, tuned.versions.lexicon) == (1, 1)
    bare = await tuned_for(kept, "nobody", SANDBOX, None, AGENT, AgentConfig(slug=AGENT))
    assert bare.versions.config is None and bare.versions.lexicon is None
