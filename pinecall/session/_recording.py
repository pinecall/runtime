"""A call's audio recorded by its own session: the caller and the agent, as they were heard."""

from pathlib import Path

from livekit.agents import AgentSession
from livekit.agents.voice.recorder_io import RecorderIO

# livekit's recorder wraps the session's audio in and out and places both on one timeline: a
# stereo Ogg Opus, the caller on the left and the agent on the right, encoded in a thread of this
# process (~2.4 % of a core a call). The room's recorder (egress) ran a process of its own per call
# to decode, mix and encode again what this process already holds (~0.12 vCPU, infra/lab/). The
# hold melody is a track of its own, not the session's output: the recording is the conversation.
# The session closes the recorder before the seal, so the file is whole when the summary names it.


async def recorded(live: AgentSession[None], audio: Path | None) -> RecorderIO | None:
    """Record the session's two sides into the file from now on; None when it keeps no audio."""
    caller, agent = live.input.audio, live.output.audio
    if audio is None or caller is None or agent is None:
        return None
    recorder = RecorderIO(agent_session=live)
    live.input.audio = recorder.record_input(caller)
    live.output.audio = recorder.record_output(agent)
    await recorder.start(output_path=audio)
    return recorder
