"""A tool's read-back, said in the first gap and never over a sentence somebody is still hearing."""

from __future__ import annotations

from typing import Any

from livekit.agents.voice.speech_handle import SpeechHandle


# `say()` has one behaviour and it is to CUT whatever is playing — there is no parameter that asks
# it to wait. And something usually is playing: preemptive generation starts the model's reply
# before the tool's output has settled, so by the time a tool returns, the reply is on the line.
# Saying the read-back then cut it, and a cut sentence that nothing follows is a FALSE
# interruption, which livekit answers by playing the sentence again. Bernardo booked an
# appointment and heard the goodbye four times: the same speech_id and the same metrics to the
# millisecond, which is a replay and not a model repeating itself.
#
# So the read-back waits for its gap instead of making one. `add_done_callback` fires when the
# speech ends however it ended, and asking again from inside it is what handles the case where the
# caller has already started the next turn: it lands at the first silence, and never on top of
# somebody. There is no timeout and none is wanted — a read-back says what was DONE, and a caller
# who books an appointment is told so whenever the line is free, not dropped because they talked.
def read_back(live: Any, text: str) -> None:
    """Say it in the first gap, once. Waits again because the gap it waited for can be gone."""
    spoken = False

    def when_the_line_is_free(_finished: object = None) -> None:
        # Once, whatever wakes it. Nothing here should fire a handle's callbacks twice, and a
        # read-back heard twice is the bug this whole file is about: the guard costs a boolean.
        nonlocal spoken
        if spoken:
            return
        playing: SpeechHandle | None = live.current_speech
        if playing is None or playing.done():
            spoken = True
            live.say(text)
            return
        playing.add_done_callback(when_the_line_is_free)

    when_the_line_is_free()
