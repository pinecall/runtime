"""One simulated caller on a real line: a room, the agent dispatched into it, and a voice on it."""

from __future__ import annotations

import json
import logging
import random as randomness
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from livekit import api, rtc
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest
from livekit.protocol.room import DeleteRoomRequest

from pinecall._settings import Settings
from pinecall.auth.scopes import a_room_token, secret_for
from pinecall.evals import line as degrading
from pinecall.evals import speech
from pinecall.evals.polling import until
from pinecall.types import PRODUCTION, Env
from pinecall.types.dispatch import (
    AGENT_KEY,
    APP_KEY,
    CALLER_KEY,
    ENV_KEY,
    HOLDER_KEY,
    ORG_KEY,
    RUN_KEY,
    WORKER_NAME,
)

logger = logging.getLogger(__name__)

# The identity the caller joins under. `pinecall-runtime worker talk` is livekit's console and
# opens the machine's own microphone; this caller never does — its microphone is a file the box
# wrote a moment ago, which is what makes a `--voice` run safe to start from a card.
A_SIMULATED_CALLER = "simulated_caller"

# How long the caller waits for the dispatched job to be listening before giving up. A worker that
# is not running is the usual reason, and it is worth saying so rather than holding an empty room.
# Measured: a cold box whose room connection needed one retry took 15s to have ears.
THE_AGENT_MAY_TAKE_S = 40.0

# How long the caller's seat in the room is good for: longer than any run holds a line, so the
# token never ends a call that the run's own deadlines had not.
A_CALL_MAY_LAST_S = 15 * 60.0

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


# What the caller does after EVERY line it says — between two of them, and after the last one
# before the line drops. It is given how many lines have been said so far and waits for the agent
# to have finished answering them: a golden that expects a tool on its last turn would otherwise be
# judged on a call that ended mid-thought, and its SECOND line would be said over the answer to its
# first. There is no clock to fall back on: a caller that guesses talks over the agent.
type Settled = Callable[[int], Awaitable[None]]


class Speaks(Protocol):
    """Whoever says the caller's lines out loud: the room's mouth, or a test's notebook."""

    async def say(self, text: str) -> None:
        """One line, said."""
        ...


async def a_simulated_call(
    call: str,
    agent: str,
    *,
    turns: int,
    next_line: NextLine,
    line: Line,
    settings: Settings,
    fleet: str = WORKER_NAME,
    settled: Settled,
    org: str,
    env: Env,
    holder: str | None = None,
    caller: str | None = None,
    run: str | None = None,
    app: str | None = None,
) -> int:
    """Open the room, dispatch the agent into it, say the turns out loud, and hang up."""
    if line.interferer_db is not None and not line.interferer:
        line.interferer = await speech.spoken(speech.A_TELEVISION)
    async with _dispatch(call, agent, fleet, settings, caller, run, app, org, env, holder):
        room = rtc.Room()
        await room.connect(settings.livekit_url, _a_token(call, settings))
        try:
            # In the order of a phone call: the caller is on the line, then somebody picks up, then
            # the caller speaks. The line is held open until the run says the answer has landed.
            mouth = await _Mouth.on(room, line)
            await _until_the_agent_is_here(room, call)
            spoken = await every_turn(mouth, turns, next_line, settled)
            # Held once more before the hangup, which ENDS the job: a caller that said it was
            # leaving broke out of `every_turn` without waiting, and the agent's answer to that
            # last line would be cut mid-word — and cut out of the recording with it. `settled`
            # answers at once when the agent is already listening, so a call that is over pays
            # nothing, and it gives up on its own deadline rather than raising (polling.py).
            await settled(spoken)
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
        run: str | None,
        app: str | None,
        org: str,
        env: Env,
        holder: str | None,
    ) -> None:
        self._call = call
        self._agent = agent
        self._fleet = fleet
        self._settings = settings
        self._caller = caller
        self._run = run
        self._app = app
        self._whose = (org, env, holder)
        self._api: api.LiveKitAPI | None = None

    # The dispatch is what puts the agent in the room: a room job whose metadata names the agent,
    # which is the door `worker/router.py:arrival_of` reads first and the one an outbound call
    # already arrives through. Nothing here dials anything and no SIP leg is waited for.
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

    # A caller and a run are named only when there is a reason to. Ring 2 says which run opened the
    # call, so the worker and the app treat it as a written eval call: no greeting, the golden's
    # state seeded. A plain simulate names neither, and the router falls back to the room.
    def _metadata(self) -> dict[str, str]:
        """What the dispatch tells the worker: the agent, the caller, and which run opened it."""
        # Whose call it is, the same three words `POST /v1/tokens` writes: the one worker every
        # org shares resolves the agent, the org's provider keys and the log in THIS corner. A
        # dispatch that named only the agent sent the worker looking in its own org — the box's,
        # which holds nobody's agents — and every simulated call but org `default`'s died with
        # NoRoute (2026-09-16, the first spoken call of another org's agent).
        org, env, holder = self._whose
        said = {AGENT_KEY: self._agent, ORG_KEY: org, ENV_KEY: env}
        if holder is not None:
            said[HOLDER_KEY] = holder
        if self._caller is not None:
            said[CALLER_KEY] = self._caller
        if self._run is not None:
            said[RUN_KEY] = self._run
        if self._app is not None:
            said[APP_KEY] = self._app
        return said

    # A caller leaving is not a hangup. The agent is still seated and its job still running, so
    # `add_shutdown_callback` never fires — and that callback is where the log is SEALED and the
    # recording's path is stated (worker/entry.py:95,191). The pointer lives in `call.summary` and
    # nowhere else (api/calls/recording.py:17), so every simulated call left its audio on disk and
    # out of reach: 1.6 MB of ogg on the box, answered with "has no call.summary yet". A phone call
    # ends itself when the leg hangs up and livekit closes the room; a simulation has to hang up.
    async def __aexit__(self, *_closed: object) -> None:
        if self._api is None:
            return
        try:
            await self._api.room.delete_room(DeleteRoomRequest(room=self._call))
        except api.TwirpError:
            # Already gone: the agent hung up first, which seals the call by the same door.
            logger.debug("room %s was closed before the caller hung up", self._call)
        finally:
            await self._api.aclose()


def _dispatch(
    call: str,
    agent: str,
    fleet: str,
    settings: Settings,
    caller: str | None = None,
    run: str | None = None,
    app: str | None = None,
    org: str = "",
    env: Env = PRODUCTION,
    holder: str | None = None,
) -> _Dispatch:
    """The agent asked into this room for the length of the call."""
    return _Dispatch(call, agent, fleet, settings, caller, run, app, org, env, holder)


class _Mouth:
    """The caller's own track: published once, at the room's rate, before a word of it exists."""

    def __init__(self, source: rtc.AudioSource, line: Line) -> None:
        self._source = source
        self._line = line

    # Published BEFORE the agent is waited for. A room's audio flows because somebody subscribed
    # to a track, and a subscription is signalled, negotiated and then opened: a track opened and
    # pushed into in one breath handed the agent a line already playing, and the 1.7 seconds it took
    # to subscribe were the caller's whole first sentence (2026-09-11, `identifica-al-paciente`, one
    # run in three). The three lines are livekit's own (examples/primitives/echo-agent.py:45-50).
    @classmethod
    async def on(cls, room: rtc.Room, line: Line) -> _Mouth:
        """The caller's microphone in this room: open, silent, and kept for the whole call."""
        source = rtc.AudioSource(sample_rate=speech.SAMPLE_RATE, num_channels=speech.CHANNELS)
        track = rtc.LocalAudioTrack.create_audio_track(A_SIMULATED_CALLER, source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        return cls(source, line)

    async def say(self, text: str) -> None:
        """One line spoken by the box, mixed with the interferer, and pushed frame by frame."""
        pcm = await speech.spoken(text)
        if self._line.interferer_db is not None:
            pcm = degrading.mixed(pcm, self._line.interferer, self._line.interferer_db)
        frames = degrading.with_losses(
            degrading.frames_of(pcm, speech.SAMPLE_RATE), self._line.packet_loss, self._line.random
        )
        # No sleep between frames: livekit's own publisher pushes them straight into the source and
        # lets it pace them (examples/primitives/echo-agent.py:94), and its queue is a second of
        # audio. Sleeping here as well would put the caller on the line at half speed.
        for frame in frames:
            await self._source.capture_frame(
                rtc.AudioFrame(
                    data=frame,
                    sample_rate=speech.SAMPLE_RATE,
                    num_channels=speech.CHANNELS,
                    samples_per_channel=len(frame) // 2,
                )
            )


# A talk token, because the caller publishes a microphone: a participate token reads a call and
# publishes nothing, which is exactly what the grants table says of it.
def _a_token(call: str, settings: Settings) -> str:
    """The caller's seat in the room, signed with the pair every call token is signed with."""
    return a_room_token(
        call, "talk", time.time() + A_CALL_MAY_LAST_S, secret_for(settings), A_SIMULATED_CALLER
    )


# Being in the room is not being ready to hear: the job joins, and its session — the ears, the
# model and the voice — is built after. On a box whose media plane needed a retry the gap was 15
# seconds, and a caller that talked into it said both its lines to nobody. The agent's own audio
# track is what says the pipeline is live, so that is what is waited for.
async def _until_the_agent_is_here(room: rtc.Room, call: str) -> None:
    """Wait for the dispatched job to be LISTENING, or say plainly that nobody answered."""

    async def listening() -> bool:
        return _listening(room)

    if not await until(listening, within_s=THE_AGENT_MAY_TAKE_S):
        raise TimeoutError(NOBODY_ANSWERED.format(call=call, seconds=THE_AGENT_MAY_TAKE_S))


def _listening(room: rtc.Room) -> bool:
    """Whether somebody in the room has published audio: the agent's own voice, which it publishes
    when its session starts. Presence alone is a job that has joined and is still building."""
    return any(
        publication.kind == rtc.TrackKind.KIND_AUDIO
        for participant in room.remote_participants.values()
        for publication in participant.track_publications.values()
    )


async def every_turn(mouth: Speaks, turns: int, next_line: NextLine, settled: Settled) -> int:
    """The caller's turns, said out loud one at a time, each waiting for the answer to the last."""
    spoken = 0
    for turn in range(turns):
        text, hanging_up = await next_line(turns - turn)
        if not text:
            break
        await mouth.say(text)
        spoken += 1
        if hanging_up:
            break
        # A caller that guessed said its second line over the answer to its first:
        # `reserva-cuando-el-paciente-dice-que-si` was heard once out of twice, every spoken run,
        # until this waited for the agent instead of six seconds (2026-09-11).
        await settled(spoken)
    return spoken
