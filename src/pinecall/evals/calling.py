"""One simulated caller on a real line: a room, the agent dispatched into it, and a voice on it."""

from __future__ import annotations

import asyncio
import json
import logging
import random as randomness
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from livekit import api, rtc
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

from pinecall._settings import Settings
from pinecall.auth.scopes import a_room_token, secret_for
from pinecall.evals import line as degrading
from pinecall.evals import speech
from pinecall.types.dispatch import AGENT_KEY, APP_KEY, CALLER_KEY, WORKER_NAME

logger = logging.getLogger(__name__)

# The identity the caller joins under. `pinecall-runtime worker talk` is livekit's console and
# opens the machine's own microphone; this caller never does — its microphone is a file the box
# wrote a moment ago, which is what makes a `--voice` run safe to start from a card.
A_SIMULATED_CALLER = "simulated_caller"

# How long the caller waits for the dispatched job to be listening before giving up. A worker that
# is not running is the usual reason, and it is worth saying so rather than holding an empty room.
# Measured: a cold box whose room connection needed one retry took 15s to have ears.
THE_AGENT_MAY_TAKE_S = 40.0

# How long the caller listens after saying something. A whole turn is stt, the model and a voice;
# what the agent actually said is read off the call's own log by whoever is watching it, so this
# side only has to leave a silence long enough for one to happen.
A_LISTENING_SILENCE_S = 6.0

NOBODY_ANSWERED = "no agent joined room {call} in {seconds:.0f}s: is `pinecall-runtime worker` up?"


@dataclass
class Line:
    """How the caller's line is spoiled on purpose, and what a report says about it."""

    # None is a clean line: no interferer is synthesized and nothing is mixed into the caller.
    interferer_db: float | None = None
    packet_loss: float = 0.0
    interferer: bytes = b""
    random: randomness.Random = field(default_factory=randomness.Random)

    @property
    def said(self) -> str:
        """The one sentence a report prints about the line this call was held on."""
        if self.interferer_db is None and self.packet_loss <= 0:
            return "a clean line"
        return degrading.said_of(self.interferer_db or 0.0, self.packet_loss)


type NextLine = Callable[[int], Awaitable[tuple[str, bool]]]
"""What the caller says next, asked of whoever holds the persona: the line, and the hangup.

It is given how many turns are left and nothing else: the conversation so far is in the call's own
log, and whoever holds the persona reads it back from there — the same reason ring 4's judges read
the log rather than a session's history (docs/decisions/scoring.md)."""


# What a run does after the caller's last word and before the line drops: ring 2 waits for the
# agent to finish answering, because a golden that expects a tool on the last turn would otherwise
# be judged on a call that ended mid-thought. A plain simulate passes none and hangs up.
type Settled = Callable[[], Awaitable[None]]


async def a_simulated_call(
    call: str,
    agent: str,
    *,
    turns: int,
    next_line: NextLine,
    line: Line,
    settings: Settings,
    fleet: str = WORKER_NAME,
    caller: str | None = None,
    app: str | None = None,
    settled: Settled | None = None,
) -> int:
    """Open the room, dispatch the agent into it, say the turns out loud, and hang up."""
    if line.interferer_db is not None and not line.interferer:
        line.interferer = (await speech.spoken(speech.A_TELEVISION)).pcm
    async with _dispatch(call, agent, fleet, settings, caller, app):
        room = rtc.Room()
        await room.connect(settings.livekit_url, _a_token(call, settings))
        try:
            await _until_the_agent_is_here(room, call)
            spoken = await _every_turn(_Mouth(room, line), turns, next_line)
            # Held open on purpose: the line drops when the run says the answer landed, not when
            # the caller stops talking.
            if settled is not None:
                await settled()
            return spoken
        finally:
            await room.disconnect()


class _Dispatch:
    """One `create_dispatch`, held open for the call and closed with the API client after it."""

    def __init__(
        self,
        call: str,
        agent: str,
        fleet: str,
        settings: Settings,
        caller: str | None,
        app: str | None,
    ) -> None:
        self._call = call
        self._agent = agent
        self._fleet = fleet
        self._settings = settings
        self._caller = caller
        self._app = app
        self._api: api.LiveKitAPI | None = None

    # The dispatch is what puts the agent in the room: a room job whose metadata names the agent,
    # which is the door `worker/router.py:52-56` reads first and the one an outbound call already
    # arrives through. Nothing here dials anything and no SIP leg is waited for.
    async def __aenter__(self) -> None:
        self._api = api.LiveKitAPI(
            self._settings.livekit_url,
            self._settings.livekit_api_key,
            self._settings.livekit_api_secret,
        )
        await self._api.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=self._call,
                agent_name=self._fleet,
                metadata=json.dumps(self._metadata()),
            )
        )

    # A caller is named only when the run has a reason to: ring 2 marks its calls as eval callers
    # so the app seeds the golden's state, exactly as a written eval call does. A plain simulate
    # names nobody and the router falls back to the room, which is what it always did.
    def _metadata(self) -> dict[str, str]:
        """What the dispatch tells the worker: the agent, and who it should say is calling."""
        said = {AGENT_KEY: self._agent}
        if self._caller is not None:
            said[CALLER_KEY] = self._caller
        if self._app is not None:
            said[APP_KEY] = self._app
        return said

    async def __aexit__(self, *_closed: object) -> None:
        if self._api is not None:
            await self._api.aclose()


def _dispatch(
    call: str,
    agent: str,
    fleet: str,
    settings: Settings,
    caller: str | None = None,
    app: str | None = None,
) -> _Dispatch:
    """The agent asked into this room for the length of the call."""
    return _Dispatch(call=call, agent=agent, fleet=fleet, settings=settings, caller=caller, app=app)


class _Mouth:
    """The caller's own track: published once, at the rate the box's speech tool actually writes."""

    def __init__(self, room: rtc.Room, line: Line) -> None:
        self._room = room
        self._line = line
        self._source: rtc.AudioSource | None = None

    async def say(self, text: str) -> None:
        """One line spoken by the box, mixed with the interferer, and published frame by frame."""
        said = await speech.spoken(text)
        pcm = (
            said.pcm
            if self._line.interferer_db is None
            else degrading.mixed(said.pcm, self._line.interferer, self._line.interferer_db)
        )
        frames = degrading.with_losses(
            degrading.frames_of(pcm, said.sample_rate), self._line.packet_loss, self._line.random
        )
        source = await self._published(said.sample_rate)
        # No sleep between frames: livekit's own publisher pushes them straight into the source and
        # lets it pace them (examples/primitives/echo-agent.py:94), and its queue is a second of
        # audio. Sleeping here as well would put the caller on the line at half speed.
        for frame in frames:
            await source.capture_frame(
                rtc.AudioFrame(
                    data=frame,
                    sample_rate=said.sample_rate,
                    num_channels=speech.CHANNELS,
                    samples_per_channel=len(frame) // 2,
                )
            )

    # The track is published at the FIRST thing the caller says, because the rate is the speech
    # tool's own and this is where it is first known. The three lines are livekit's own
    # (examples/primitives/echo-agent.py:45-50), down to the microphone source.
    async def _published(self, sample_rate: int) -> rtc.AudioSource:
        """The caller's microphone in this room, opened once and kept for the whole call."""
        if self._source is None:
            self._source = rtc.AudioSource(sample_rate=sample_rate, num_channels=speech.CHANNELS)
            track = rtc.LocalAudioTrack.create_audio_track(A_SIMULATED_CALLER, self._source)
            await self._room.local_participant.publish_track(
                track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            )
        return self._source


# A talk token, because the caller publishes a microphone: a participate token reads a call and
# publishes nothing, which is exactly what the grants table says of it.
def _a_token(call: str, settings: Settings) -> str:
    """The caller's seat in the room, signed with the pair every call token is signed with."""
    for_the_whole_call = THE_AGENT_MAY_TAKE_S + A_LISTENING_SILENCE_S * 60
    return a_room_token(
        call, "talk", time.time() + for_the_whole_call, secret_for(settings), A_SIMULATED_CALLER
    )


# Being in the room is not being ready to hear: the job joins, and its session — the ears, the
# model and the voice — is built after. On a box whose media plane needed a retry the gap was 15
# seconds, and a caller that talked into it said both its lines to nobody. The agent's own audio
# track is what says the pipeline is live, so that is what is waited for.
async def _until_the_agent_is_here(room: rtc.Room, call: str) -> None:
    """Wait for the dispatched job to be LISTENING, or say plainly that nobody answered."""
    deadline = time.monotonic() + THE_AGENT_MAY_TAKE_S
    while not _listening(room) and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    if not _listening(room):
        raise TimeoutError(NOBODY_ANSWERED.format(call=call, seconds=THE_AGENT_MAY_TAKE_S))


def _listening(room: rtc.Room) -> bool:
    """Whether somebody in the room has published audio: the agent's own voice, which it publishes
    when its session starts. Presence alone is a job that has joined and is still building."""
    return any(
        publication.kind == rtc.TrackKind.KIND_AUDIO
        for participant in room.remote_participants.values()
        for publication in participant.track_publications.values()
    )


async def _every_turn(mouth: _Mouth, turns: int, next_line: NextLine) -> int:
    """The caller's turns, said out loud one at a time, with a silence for the answer after each."""
    spoken = 0
    for turn in range(turns):
        text, hanging_up = await next_line(turns - turn)
        if not text:
            break
        await mouth.say(text)
        spoken += 1
        await asyncio.sleep(A_LISTENING_SILENCE_S)
        if hanging_up:
            break
    return spoken
