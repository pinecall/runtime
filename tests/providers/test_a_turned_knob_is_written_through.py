"""The holder in front of the table: every turn is written through, and a load reads them back."""

from __future__ import annotations

import pytest

from pinecall.orgs.turned import MemoryTurned, turned_for
from pinecall.providers.overrides import Overridden, Overrides

pytestmark = pytest.mark.unit

AN_ORG = "clinica"
AN_AGENT = "clinica-norte"
A_MODEL = "anthropic/claude-haiku-4-5"


async def test_a_holder_with_nothing_behind_it_still_turns_a_knob() -> None:
    """A gateway on a dev key keeps no table, and the console must work there exactly the same."""
    alone = Overrides()
    await alone.set(AN_ORG, AN_AGENT, Overridden(llm=A_MODEL))

    assert alone.of(AN_AGENT).llm == A_MODEL


async def test_what_was_turned_is_written_through_and_read_back() -> None:
    kept = MemoryTurned()
    await Overrides(kept).set(AN_ORG, AN_AGENT, Overridden(llm=A_MODEL, greeting="Buenas."))

    started = Overrides(kept)
    await started.loaded()

    assert started.of(AN_AGENT) == Overridden(llm=A_MODEL, greeting="Buenas.")


async def test_a_process_that_loads_nothing_reads_every_agent_as_untouched() -> None:
    started = Overrides(MemoryTurned())
    await started.loaded()

    assert started.of(AN_AGENT) == Overridden()


def test_a_gateway_with_no_pool_keeps_its_knobs_in_this_process() -> None:
    """The table is the pool's; without one the holder is what it always was, and says so."""
    assert isinstance(turned_for(None), MemoryTurned)
