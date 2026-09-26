"""A log outlives the shape of its entries, and a reader of one version must read all of them."""

import pytest

from pinecall.log.reduce import UNREADABLE, apply, initial_state
from pinecall_protocol.envelope import Entry

pytestmark = pytest.mark.unit

# `prompt.changed` carried `region` before it carried `name`, and those rows are still in the
# table. A reader that raised on one refused the whole call it belonged to.
FROM_ANOTHER_VERSION = Entry(
    seq=7,
    ts=1.0,
    call="CA_8f4a2c",
    agent="clinica-norte",
    type="prompt.changed",
    ephemeral=False,
    data={"region": "static", "hash": "abc", "chars": 10},
)


def test_an_entry_this_reader_cannot_read_is_one_line_of_the_errors_list() -> None:
    state = apply(initial_state(), FROM_ANOTHER_VERSION)
    assert len(state.errors) == 1
    assert state.errors[0].code == UNREADABLE
    assert "prompt.changed at seq 7" in state.errors[0].message
    assert state.prompt == {}


def test_the_fold_still_moves_to_the_seq_it_could_not_read() -> None:
    state = apply(initial_state(), FROM_ANOTHER_VERSION)
    assert state.seq == 7
    assert state.agent == "clinica-norte"
    assert state.call == "CA_8f4a2c"


def test_an_entry_of_a_type_nobody_knows_is_refused_the_same_way() -> None:
    unknown = FROM_ANOTHER_VERSION.model_copy(update={"type": "nobody.knows"})
    state = apply(initial_state(), unknown)
    assert "nobody.knows at seq 7" in state.errors[0].message


def test_the_entries_around_it_are_folded_as_if_it_had_not_been_there() -> None:
    started = Entry(
        seq=8,
        ts=2.0,
        call="CA_8f4a2c",
        agent="clinica-norte",
        type="call.started",
        ephemeral=False,
        data={
            "channel": "web",
            "direction": "inbound",
            "from": "web_1",
            "to": "clinica-norte",
            "caller": None,
            "started_at": 2.0,
        },
    )
    state = apply(apply(initial_state(), FROM_ANOTHER_VERSION), started)
    assert state.status == "active"
    assert state.seq == 8
    assert len(state.errors) == 1
