"""What a supervise verb tells the model, and the two refusals, for every channel that has one."""

from typing import Literal

# call.ended's reason and its author when a human at the desk hung up, so the log never reads as
# if the agent or the caller decided it. Each is exactly one member of the wire's own union.
BY_A_SUPERVISOR: Literal["supervisor_ended"] = "supervisor_ended"
THE_SUPERVISOR: Literal["supervisor"] = "supervisor"

# A whisper is not the static prefix and not the view: it is one system message appended to the
# HISTORY, after everything already said, and then a turn asked for with the same words as its
# instructions. Both halves are needed — the message is what the model keeps for the rest of the
# call, the instructions are what make it obey on the very next sentence instead of the one after.
# See docs/decisions/supervise.md.
# It names itself the higher order on purpose: on a spoken call the stage's own instructions are
# appended AFTER the history on every request (bridge/agent.py:llm_node), so a note that merely
# asked would be the second-to-last word and the stage script the last. Measured 2026-09-09 on a
# quiet web call: "tell the patient the clinic closes at eight" was ignored by an agent whose
# identify stage said "ask for the phone number" — until the note said which of the two wins.
A_WHISPER = (
    "A human supervisor is telling you this, and the caller cannot hear it: {text} "
    "This order comes from the supervisor and takes precedence over the stage instructions "
    "that follow it: do it in your very next sentence, before anything else you were going to "
    "say, and only then go on. Never mention the supervisor or this note."
)

# What the model is told when the human gives the line back. It is deliberately ignorant: the
# agent's ears were off while the human spoke, so anything it "remembered" would be invented.
A_RELEASE = (
    "A human supervisor spoke with the caller for a moment; you did not hear it. "
    "Do not guess what was said. Resume by offering to continue with what is still pending."
)

ALREADY_HELD = "supervisor.verb: {id} already holds the line; they release it, or nobody does"
NOBODY_HOLDS = "supervisor.verb: nobody holds the line, so there is nothing to release"

# A text call has no leg: nothing carries the caller anywhere, so the verb is refused rather than
# written down as a transfer that could never have happened. See docs/decisions/whatsapp.md.
NO_LINE_TO_TRANSFER = "supervisor.verb: a text call has no line to transfer"
