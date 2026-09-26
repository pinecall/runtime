"""The three verbs against a database that answers from a list: list, show, and tail's loop."""

import argparse
import json
from io import StringIO
from typing import Any

import pytest

from pinecall.cli.sessions import verbs as sessions
from pinecall_protocol import decode_entries
from pinecall_protocol.envelope import Entry
from pinecall_protocol.fixtures import GOLDEN_LOG

pytestmark = pytest.mark.unit

THE_GOLDEN_CALL = "CA_8f4a2c"


@pytest.fixture(scope="module")
def golden() -> list[Entry]:
    return decode_entries(GOLDEN_LOG.read_text(encoding="utf-8"))


class Scripted:
    """A Calls the tests own: it answers from what they put in it, a page per question, in order."""

    def __init__(self, pages: dict[str, list[list[Entry]]], live: str | None = None) -> None:
        self.pages = pages
        self.live = live
        self.asked = 0

    async def calls(self, agent: str | None, limit: int) -> list[str]:
        listed = [call for call in self.pages if agent is None or call.startswith(agent)]
        return listed[:limit]

    async def newest_live_call(self) -> str | None:
        return self.live

    # after= is the store's cursor; this fake hands out its pages in order and ignores it.
    async def entries(self, call: str, *, after: int = 0) -> list[Entry]:  # noqa: ARG002
        self.asked += 1
        remaining = self.pages.get(call, [])
        return remaining.pop(0) if remaining else []


async def never_sleeps(_seconds: float) -> None:
    """tail's poll, with the waiting taken out: a test must not spend a quarter of a second."""


async def presses_control_c(_seconds: float) -> None:
    """What SIGINT does to a process parked in the poll: Python raises it out of the sleep."""
    raise KeyboardInterrupt


async def test_the_listing_puts_one_line_per_call_with_its_cost(golden: list[Entry]) -> None:
    """What `sessions list` is for: the call, when, how long, how it went, what it cost."""
    source = Scripted({THE_GOLDEN_CALL: [golden]})
    out = StringIO()
    assert await sessions.list_calls(None, 20, source, out) == 0
    line = out.getvalue().strip()
    assert line.startswith(f"{THE_GOLDEN_CALL}  phone  2026-08-12 ")
    assert "52.0s" in line
    assert "booked BK-5521" in line
    assert line.endswith("0.0229 EUR")


async def test_a_call_with_no_summary_is_listed_unpriced(golden: list[Entry]) -> None:
    """A call still running has no cost, and `unpriced` says so where a 0.00 would lie."""
    running = [entry for entry in golden if entry.type != "call.summary"]
    source = Scripted({THE_GOLDEN_CALL: [running]})
    out = StringIO()
    assert await sessions.list_calls(None, 20, source, out) == 0
    assert out.getvalue().strip().endswith("unpriced")


async def test_nothing_to_list_is_a_sentence() -> None:
    """An empty screen is a bug report; a sentence naming the agent is an answer."""
    out = StringIO()
    assert await sessions.list_calls("clinica-norte", 20, Scripted({}), out) == 0
    assert out.getvalue() == "no calls yet for clinica-norte\n"


async def test_show_prints_the_transcript_and_ends_with_the_medians(golden: list[Entry]) -> None:
    """Acceptance 1, through the verb: 124 entries in, the transcript and its table out."""
    out = StringIO()
    assert (
        await sessions.show_call(THE_GOLDEN_CALL, Scripted({THE_GOLDEN_CALL: [golden]}), out) == 0
    )
    printed = out.getvalue().splitlines()
    assert printed[0].split()[:3] == ["1", "+0.000", "call.ringing"]
    assert printed[-6] == "medians"
    assert printed[-1].startswith("  e2e_latency")


async def test_show_json_prints_the_state_the_log_reduces_to(golden: list[Entry]) -> None:
    """--json is the same log, folded: what a script reads instead of parsing the transcript."""
    out = StringIO()
    source = Scripted({THE_GOLDEN_CALL: [golden]})
    assert await sessions.show_call(THE_GOLDEN_CALL, source, out, as_json=True) == 0
    state = json.loads(out.getvalue())
    assert state["status"] == "ended"
    assert state["outcome"].startswith("booked BK-5521")
    assert len(state["turns"]) == 12


async def test_an_id_nobody_wrote_under_is_an_error_and_says_so() -> None:
    """A typo must not read as an empty call: exit 1, and the id back so the reader sees it."""
    out = StringIO()
    assert await sessions.show_call("CA_nope", Scripted({}), out) == 1
    assert out.getvalue() == "no call CA_nope in the log\n"


async def test_tail_prints_what_arrives_and_stops_at_the_verdict(golden: list[Entry]) -> None:
    """The loop's contract: poll, print what is new, end when the call's last entry lands."""
    first, rest = golden[:10], golden[10:]
    source = Scripted({THE_GOLDEN_CALL: [first, [], rest]})
    out = StringIO()
    exit_code = await sessions.tail_call(THE_GOLDEN_CALL, source, out, sleep=never_sleeps)
    assert exit_code == 0
    printed = out.getvalue().splitlines()
    assert printed[0].split()[:3] == ["1", "+0.000", "call.ringing"]
    assert printed[-1].split()[:3] == ["125", "+53.461", "log.caught_up"]
    # Three polls: the backlog, the empty one, and the page the verdict came in.
    assert source.asked == 3


async def test_tail_without_an_id_follows_the_newest_live_call(golden: list[Entry]) -> None:
    """Nobody types a call id mid-call; the newest unsealed log is the one they meant."""
    source = Scripted({THE_GOLDEN_CALL: [golden]}, live=THE_GOLDEN_CALL)
    out = StringIO()
    assert await sessions.tail_call(None, source, out, sleep=never_sleeps) == 0
    assert THE_GOLDEN_CALL not in out.getvalue().splitlines()[0]


async def test_tail_with_nothing_live_says_so_and_answers_one() -> None:
    """Following nothing is not success: the exit code is what a script watching a box reads."""
    out = StringIO()
    assert await sessions.tail_call(None, Scripted({}), out, sleep=never_sleeps) == 1
    assert out.getvalue() == "no live call to follow\n"


async def test_tail_leaves_the_loop_when_the_reader_presses_control_c(golden: list[Entry]) -> None:
    """SIGINT lands in the poll, and it leaves through the loop rather than through a swallow."""
    source = Scripted({THE_GOLDEN_CALL: [golden[:5]]})
    with pytest.raises(KeyboardInterrupt):
        await sessions.tail_call(THE_GOLDEN_CALL, source, StringIO(), sleep=presses_control_c)


def test_control_c_is_exit_zero_and_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Acceptance 2: Ctrl+C out of `sessions tail` is a clean exit, never a stack trace."""

    def interrupted(_verb: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(sessions, "_against_the_database", interrupted)
    assert sessions.run_tail(argparse.Namespace(call=THE_GOLDEN_CALL)) == 0
    assert capsys.readouterr().err == ""


async def test_recording_prints_the_path_the_summary_carries_and_nothing_else(
    golden: list[Entry],
) -> None:
    """Criterion 3: one line, the path alone, so `-o $(sessions recording <id>)` is the download."""
    out = StringIO()
    source = Scripted({THE_GOLDEN_CALL: [golden]})
    assert await sessions.recording_of(THE_GOLDEN_CALL, source, out) == 0
    assert out.getvalue() == "recordings/2026/08/12/CA_8f4a2c.ogg\n"


async def test_a_call_still_running_has_no_recording_to_name_yet(golden: list[Entry]) -> None:
    """The path is stated in call.summary, so a call without one is told that, not a blank."""
    running = [entry for entry in golden if entry.type != "call.summary"]
    out = StringIO()
    source = Scripted({THE_GOLDEN_CALL: [running]})
    assert await sessions.recording_of(THE_GOLDEN_CALL, source, out) == 1
    assert out.getvalue() == (
        f"call {THE_GOLDEN_CALL} has no call.summary yet: "
        "the recording is stated when the call ends\n"
    )


async def test_a_call_the_box_did_not_record_says_so_and_answers_one(golden: list[Entry]) -> None:
    """A box with recording off writes a summary with no path, and a blank is not an answer."""
    unrecorded = [_without_the_recording(entry) for entry in golden]
    out = StringIO()
    source = Scripted({THE_GOLDEN_CALL: [unrecorded]})
    assert await sessions.recording_of(THE_GOLDEN_CALL, source, out) == 1
    assert out.getvalue().endswith("carries no path\n")


async def test_recording_of_an_id_nobody_wrote_under_is_the_same_answer_show_gives() -> None:
    """One sentence for one mistake: a typo reads the same whichever verb was typed."""
    out = StringIO()
    assert await sessions.recording_of("CA_nope", Scripted({}), out) == 1
    assert out.getvalue() == "no call CA_nope in the log\n"


def _without_the_recording(entry: Entry) -> Entry:
    """The same log, from a box that keeps no audio: the summary states everything but a path."""
    if entry.type != "call.summary":
        return entry
    kept = {name: value for name, value in entry.data.items() if name != "recording"}
    return entry.model_copy(update={"data": kept})
