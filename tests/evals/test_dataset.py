"""Tests for the dataset: a finished call made a golden, kept, picked for a run, forgotten."""

import pytest

from pinecall.domain.errors import Conflict, NotFound
from pinecall.evals import dataset
from pinecall.evals.dataset import Promoted, golden_of
from pinecall.postgres.pool import Pool
from pinecall.tenancy.orgs import create
from pinecall.wire.rest.evals import Expect
from tests.conftest import postgres
from tests.evals.conftest import a_log, agent, caller

AGENT = "clinica-norte"


def test_a_golden_is_the_callers_lines_the_state_it_opened_in_and_the_apps_facts() -> None:
    log = a_log(
        ("state.changed", {"state": {"stage": "book"}, "changed": ["stage"]}),
        caller("Quiero el jueves"),
        ("event.received", {"name": "slot_freed", "data": {"at": "10:15"}, "source": "app"}),
        ("event.received", {"name": "clicked", "data": {}, "source": "participant"}),
        agent("Se liberó el de las 10:15."),
        ("state.changed", {"state": {"stage": "confirm"}, "changed": ["stage"]}),
        caller("Vale"),
        caller("   "),
    )
    golden = golden_of(log, "jueves", Expect(says=["10:15"]))
    assert golden.input == ["Quiero el jueves", "Vale"]
    assert golden.state == {"stage": "book"}
    assert [(event.after_turn, event.name) for event in golden.events] == [(1, "slot_freed")]
    assert golden.promoted_from == "CA_8f4a2c"
    assert golden.today is not None
    assert golden.expect.says == ["10:15"]


def test_a_call_whose_caller_said_nothing_is_no_case() -> None:
    with pytest.raises(Conflict, match="no line of the caller's"):
        golden_of(a_log(agent("¿Hola?")), "silence", Expect())


@postgres
async def test_a_case_is_kept_once_by_name_and_a_run_picks_what_is_not_held_out(
    pool: Pool,
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    golden = golden_of(a_log(caller("Hola")), "hola", Expect())
    kept = await dataset.promoted(
        pool, golden, AGENT, Promoted(org.id, "production", "hola", "m_ana")
    )
    kept_out = Promoted(org.id, "production", "held", "m_ana", held_out=True)
    await dataset.promoted(pool, golden, AGENT, kept_out)
    with pytest.raises(Conflict, match="named hola already"):
        await dataset.promoted(pool, golden, AGENT, Promoted(org.id, "sandbox", "hola", "m_bo"))
    assert (kept.source_call, kept.source_env, kept.author) == ("CA_8f4a2c", "production", "m_ana")
    nightly = await dataset.picked(pool, org.id, AGENT, [], every=True)
    release = await dataset.picked(pool, org.id, AGENT, ["held"], every=True)
    assert [golden.name for golden in nightly] == ["hola"]
    assert [golden.name for golden in release] == ["held", "hola"]
    with pytest.raises(NotFound, match="no case named nobody"):
        await dataset.picked(pool, org.id, AGENT, ["nobody"], every=False)
    assert [case.name for case in await dataset.listed(pool, org.id, None)] == ["held", "hola"]
    await dataset.forgotten(pool, org.id, kept.id)
    with pytest.raises(NotFound):
        await dataset.forgotten(pool, org.id, kept.id)
    with pytest.raises(NotFound):
        await dataset.forgotten(pool, "another-org", kept.id)
