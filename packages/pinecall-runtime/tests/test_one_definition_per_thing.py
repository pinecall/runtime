"""One definition per thing: the places that share a function hold the very same object."""

import pytest

from pinecall.api.evals import replay as replay_door
from pinecall.cli.sessions import source
from pinecall.evals.checks import replay as replay_check
from pinecall.log import latencies, replay, snapshots, wording
from pinecall.session.text import session, tool_runs
from pinecall.session.voice import bridge, tools

pytestmark = pytest.mark.unit


def test_every_reader_of_a_whole_call_holds_the_very_same_function() -> None:
    """The state memo, the CLI and the eval each read a call whole; there is one way to do it."""
    assert snapshots.whole is replay.whole
    assert replay_door.whole is replay.whole
    assert source.whole is replay.whole


def test_the_cli_and_the_evals_read_the_very_same_latencies() -> None:
    assert replay_check.samples is latencies.samples


def test_the_text_session_and_the_bridge_hold_the_very_same_definitions() -> None:
    """A tool's text, the outcome of a silent call, the prompt's hash: spelled once, in log/."""
    assert tool_runs.tool_result_text is wording.tool_result_text
    assert tools.tool_result_text is wording.tool_result_text
    assert session.NOTHING_SAID is wording.NOTHING_SAID
    assert bridge.NOTHING_SAID is wording.NOTHING_SAID
    assert session.hashed_prompt is wording.hashed_prompt
    assert bridge.hashed_prompt is wording.hashed_prompt
