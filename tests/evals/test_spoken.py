"""Tests for a persona on a real line: the audio it says, when it may speak, when it hangs up."""

import math
import time
from array import array

from pinecall.evals import spoken
from pinecall.evals.spoken import (
    A_SILENT_OPENING_S,
    AN_ANSWER_MAY_TAKE_S,
    FRAME_MS,
    LOST,
    SAMPLE_RATE,
    Line,
    answered,
    described,
    drop_packets,
    frames_of,
    has_answer_landed,
    is_call_over,
    is_line_open,
    mix_interferer,
    pick_caller_voice,
    resample_to_room,
    rms_of,
    speak_turns,
)
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.wire.frames import Entry
from tests.conftest import postgres

A_SECOND_IN_FRAMES = 100


# Integer samples put the level near the target, never exactly on it.
WITHIN_THE_ROUNDING_DB = 0.5


VOICES = {"acme/es": "marcos", "acme/en": "henry", "acme/fr": "amelie", "other/es": "lucia"}


LINES = ("Esa me viene bien.", "Sí, confírmemela.")


def tone_of(samples: int, amplitude: int, period: int = 100) -> bytes:
    written = array("h")
    for index in range(samples):
        written.append(int(amplitude * math.sin(2 * math.pi * index / period)))
    return written.tobytes()


def difference(mix: bytes, caller: bytes) -> bytes:
    mixed, voice = array("h"), array("h")
    mixed.frombytes(mix)
    voice.frombytes(caller)
    return array("h", (a - b for a, b in zip(mixed, voice, strict=True))).tobytes()


def a_log(*written: str | tuple[str, str]) -> list[Entry]:
    entries: list[Entry] = []
    for seq, line in enumerate(written, 1):
        kind, state = line if isinstance(line, tuple) else (line, None)
        entries.append(
            Entry(
                seq=seq,
                ts=float(seq),
                call="call_1",
                agent="clinica-norte",
                type=kind,
                ephemeral=False,
                data={"state": state} if state else {},
            )
        )
    return entries


# ── the line ──


def test_a_frame_is_ten_milliseconds_of_the_rate_it_was_cut_at() -> None:
    frames = frames_of(tone_of(SAMPLE_RATE, 8000), SAMPLE_RATE)
    assert FRAME_MS == 10
    assert len(frames) == A_SECOND_IN_FRAMES
    assert all(len(frame) == SAMPLE_RATE // A_SECOND_IN_FRAMES * 2 for frame in frames)


def test_the_last_frame_is_padded_with_silence_rather_than_sent_short() -> None:
    frames = frames_of(tone_of(SAMPLE_RATE // 100 + 10, 8000), SAMPLE_RATE)
    assert len(frames) == 2
    assert len(frames[1]) == len(frames[0])
    assert frames[1].endswith(LOST * 10)


def test_the_interferer_ends_up_the_asked_for_number_of_decibels_under_the_caller() -> None:
    caller = tone_of(SAMPLE_RATE, 8000, period=97)
    added = difference(mix_interferer(caller, tone_of(SAMPLE_RATE, 8000, period=31), 15.0), caller)
    under = 20 * math.log10(rms_of(caller) / rms_of(added))
    assert abs(under - 15.0) < WITHIN_THE_ROUNDING_DB


def test_a_quieter_interferer_is_the_larger_number_of_decibels() -> None:
    caller = tone_of(SAMPLE_RATE, 8000, period=97)
    television = tone_of(SAMPLE_RATE, 8000, period=31)
    near = difference(mix_interferer(caller, television, 15.0), caller)
    far = difference(mix_interferer(caller, television, 25.0), caller)
    assert rms_of(far) < rms_of(near)


def test_a_line_with_nothing_to_mix_in_is_the_caller_untouched() -> None:
    caller = tone_of(SAMPLE_RATE, 8000)
    assert mix_interferer(caller, b"", 15.0) == caller


def test_the_interferer_runs_as_long_as_the_caller_does() -> None:
    caller = tone_of(SAMPLE_RATE, 8000, period=97)
    with_noise = mix_interferer(caller, tone_of(SAMPLE_RATE // 10, 8000, period=31), 15.0)
    assert rms_of(difference(with_noise, caller)[-SAMPLE_RATE:]) > 0


def test_a_lost_packet_is_the_silence_a_jitter_buffer_plays() -> None:
    frames = frames_of(tone_of(SAMPLE_RATE, 8000), SAMPLE_RATE)
    thinned = drop_packets(frames, Line(packet_loss=0.5))
    assert len(thinned) == len(frames)
    # Half of a hundred, by chance: outside 20 to 80 once in a billion runs.
    assert 20 < len([frame for frame in thinned if set(frame) == {0}]) < 80


def test_no_loss_leaves_every_packet_exactly_as_it_was() -> None:
    frames = frames_of(tone_of(SAMPLE_RATE, 8000), SAMPLE_RATE)
    assert drop_packets(frames, Line()) == frames


def test_the_line_is_described_in_the_words_a_report_prints() -> None:
    assert described(Line()) == "a clean line"
    assert described(Line(interferer_db=15.0)) == "interferer 15 dB under the caller"
    assert described(Line(interferer_db=22.0, packet_loss=0.02)) == (
        "interferer 22 dB under the caller, 2% packet loss"
    )


# ── the caller's voice ──


def test_audio_already_at_the_rooms_rate_comes_back_untouched() -> None:
    audio = tone_of(480, 8000)
    assert resample_to_room(audio, SAMPLE_RATE) == audio


def test_audio_at_the_vendors_rate_comes_back_as_a_second_at_the_rooms() -> None:
    second = resample_to_room(tone_of(24_000, 8000), 24_000)
    assert abs(len(second) // 2 - SAMPLE_RATE) < SAMPLE_RATE // 50


def test_the_caller_speaks_in_the_first_voice_of_its_language_when_the_agent_has_another() -> None:
    assert pick_caller_voice(VOICES, "acme", "someone-else", "es-ES") == "marcos"


def test_an_agent_that_speaks_in_that_voice_has_its_caller_in_another() -> None:
    assert pick_caller_voice(VOICES, "acme", "marcos", "es") == "henry"


def test_a_language_with_no_voice_of_its_own_is_called_in_englishs() -> None:
    assert pick_caller_voice(VOICES, "acme", None, "de") == "henry"


def test_no_caller_voice_is_the_one_the_agent_is_given() -> None:
    for agents_voice in VOICES.values():
        assert pick_caller_voice(VOICES, "acme", agents_voice, "es") != agents_voice


def test_a_vendor_the_operator_gave_no_voice_speaks_with_its_own() -> None:
    assert pick_caller_voice(VOICES, "nobody", None, "es") is None


# ── when the caller may speak ──


def test_a_line_with_no_answer_yet_holds_the_line() -> None:
    assert not has_answer_landed(a_log("call.started", "turn.user"), 1, since=0.0)


def test_the_agent_back_to_listening_after_the_line_is_the_signal() -> None:
    log = a_log("turn.user", ("agent.state", "thinking"), ("agent.state", "listening"))
    assert has_answer_landed(log, 1, since=0.0)


def test_a_filler_turn_after_the_tool_does_not_end_the_wait() -> None:
    filler = a_log(
        "turn.user",
        ("agent.state", "thinking"),
        "tool.call",
        "tool.result",
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "thinking"),
    )
    assert not has_answer_landed(filler, 1, since=0.0)


def test_a_tool_still_running_is_the_agent_mid_turn_even_while_it_listens() -> None:
    running = a_log(
        "turn.user", ("agent.state", "thinking"), "tool.call", ("agent.state", "listening")
    )
    assert not has_answer_landed(running, 1, since=0.0)


def test_the_listening_that_came_before_the_caller_spoke_is_not_it() -> None:
    opening = a_log(("agent.state", "listening"), "turn.user", ("agent.state", "thinking"))
    assert not has_answer_landed(opening, 1, since=0.0)


def test_a_second_line_still_needs_its_own_answer() -> None:
    log = a_log("turn.user", ("agent.state", "speaking"), ("agent.state", "listening"), "turn.user")
    assert not has_answer_landed(log, 2, since=0.0)


def test_an_agent_still_greeting_keeps_the_line_closed() -> None:
    log = a_log(("agent.state", "listening"), ("agent.state", "speaking"))
    assert not is_line_open(log, now=100.0)


def test_the_greeting_said_and_the_agent_listening_opens_the_line() -> None:
    log = a_log(
        ("agent.state", "listening"),
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "listening"),
    )
    assert is_line_open(log, now=5.0)


def test_an_agent_that_opens_with_nothing_is_believed_after_a_quiet_while() -> None:
    log = a_log(("agent.state", "listening"))
    assert not is_line_open(log, now=1.0 + A_SILENT_OPENING_S - 0.1)
    assert is_line_open(log, now=1.0 + A_SILENT_OPENING_S)


def test_no_agent_in_the_room_yet_keeps_the_line_closed() -> None:
    assert not is_line_open(a_log("call.started"), now=100.0)


# A snapshot may predate the caller's line: its `listening` would be the turn before's.
def test_a_log_that_has_not_caught_up_with_the_caller_says_nothing() -> None:
    before = a_log("turn.user", ("agent.state", "thinking"), ("agent.state", "listening"))
    assert has_answer_landed(before, 1, since=0.0)
    assert not has_answer_landed(before, 1, since=99.0)


def test_a_whole_answer_to_the_previous_line_is_not_an_answer_to_this_one() -> None:
    before = a_log(
        "turn.user",
        ("agent.state", "thinking"),
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "listening"),
        "user.state",
    )
    assert not has_answer_landed(before, 2, since=5.5)


# Some ears end a turn per sentence: only the agent leaving `listening` proves it took the line.
def test_the_agent_must_have_been_handed_the_line_before_its_silence_counts() -> None:
    split = a_log(
        "turn.user",
        ("agent.state", "thinking"),
        ("agent.state", "listening"),
        "turn.user",
        "turn.user",
    )
    assert not has_answer_landed(split, 2, since=0.0)


def test_a_call_somebody_hung_up_is_over() -> None:
    assert is_call_over(a_log("call.started", "turn.user", "call.ended"))


def test_a_call_still_being_spoken_on_is_not_over() -> None:
    assert not is_call_over(a_log("call.started", "turn.user", "turn.agent"))


@postgres
async def test_the_wait_between_two_lines_ends_the_moment_the_call_does(store: Store) -> None:
    logs = Logs(store)
    log = logs.writing("call_1", "clinica-norte")
    await log.append("call.started", {})
    await log.append("turn.user", {})
    await log.append("call.ended", {})
    began = time.monotonic()
    await answered(logs, "call_1", 1)
    assert time.monotonic() - began < AN_ANSWER_MAY_TAKE_S / 2


# ── the turns ──


class Script:
    """A golden's lines handed out one per turn, and the waits between them kept."""

    def __init__(self, lines: tuple[str, ...] = LINES) -> None:
        """The lines, none said yet."""
        self.lines = lines
        self.spoken: list[str] = []
        self.waited_after: list[int] = []

    async def next_line(self, _turns_left: int) -> tuple[str, bool]:
        """The next line, never a hang-up; nothing past the last one."""
        if len(self.spoken) >= len(self.lines):
            return "", False
        return self.lines[len(self.spoken)], False

    async def say(self, line: str) -> None:
        """Keep the line."""
        self.spoken.append(line)

    async def wait(self, lines: int) -> None:
        """Keep how many lines were said when the caller waited."""
        self.waited_after.append(lines)


# A fixed delay would talk over a turn that runs a tool: that one takes over ten seconds.
async def test_the_caller_waits_for_the_answer_to_each_line_before_saying_the_next() -> None:
    script = Script()
    turns = await speak_turns(len(LINES), script.next_line, script.say, script.wait)
    assert (turns, script.spoken) == (2, list(LINES))
    assert script.waited_after == [0, 1, 2]


async def test_a_line_the_golden_does_not_have_ends_the_call_without_a_wait() -> None:
    script = Script(lines=(LINES[0],))
    turns = await speak_turns(2, script.next_line, script.say, script.wait)
    assert (turns, script.waited_after) == (1, [0, 1])


async def test_the_first_line_waits_for_the_agents_opening() -> None:
    script = Script()
    await speak_turns(len(LINES), script.next_line, script.say, script.wait)
    assert script.waited_after[0] == 0


async def test_a_caller_that_hangs_up_says_its_line_and_waits_for_nothing_more() -> None:
    script = Script()

    async def goodbye(_turns_left: int) -> tuple[str, bool]:
        return "adiós", True

    turns = await speak_turns(3, goodbye, script.say, script.wait)
    assert (turns, script.spoken, script.waited_after) == (1, ["adiós"], [0])


def test_the_room_the_caller_joins_is_the_one_it_was_given() -> None:
    line = spoken.SpokenLine("ws://box", "t", "call_1", 3, Line())
    assert (line.url, line.call, line.turns) == ("ws://box", "call_1", 3)
