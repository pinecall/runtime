"""Every session event as the entry it is: the transcripts, the states, the turns and the errors."""

from __future__ import annotations

from typing import Any, Protocol

from livekit.agents import llm as agents
from livekit.agents.types import TimedString
from livekit.agents.utils import is_given
from livekit.agents.voice import AgentSession
from livekit.agents.voice import events as session_events

from pinecall.session.voice.dead_end import is_a_dead_end
from pinecall.session.voice.metrics import Meters, an_end_of_utterance
from pinecall.session.voice.writing import Writing
from pinecall_protocol import metrics as wire
from pinecall_protocol.events import (
    AgentStateChanged,
    AgentTranscript,
    AgentTurnEnded,
    ErrorEvent,
    UserStateChanged,
    UserTranscript,
    UserTurnEnded,
)

# The session's own event names, typed as the session's own Literal: `AgentSession.on` accepts
# nothing else, so a name that livekit stopped emitting is a type error and not a dead callback.
# Named once, here, because the subscribe and the unsubscribe are two lists that must never drift.
LISTENED: tuple[session_events.EventTypes, ...] = (
    "user_input_transcribed",
    "user_state_changed",
    "agent_state_changed",
    "conversation_item_added",
    "session_usage_updated",
    "error",
)

# What a reader is told when a component of the session failed. The library's own error carries a
# vendor's message, which is exactly what a person debugging a call wants to read.
COMPONENT_FAILED = "component_failed"

# And when the same failure will come back on every retry, which is a different thing to read: the
# call is over, and this is the only error entry it will carry.
COMPONENT_DEAD_END = "component_dead_end"


class Ending(Protocol):
    """Who ends the call when a component fails for good: the bridge, and nobody else."""

    def ends_for(self, cause: str) -> None:
        """End this call as an error, naming what will not change however often it is asked."""


# One subscriber per session, holding nothing but the last thing it needs to join two facts: the
# language the recogniser reported, and the speech the reply in flight belongs to. Everything else
# is on the event itself, which is the whole reason this file is short.
class Events:
    """The session's events, turned into entries in the order livekit produced them."""

    def __init__(self, writing: Writing, meters: Meters, ending: Ending) -> None:
        self._writing = writing
        self._meters = meters
        self._ending = ending
        self._dead_end = False
        self._live: AgentSession[None] | None = None
        self._language: str | None = None
        self.turns = 0
        self.last_said = ""

    def watch(self, live: AgentSession[None]) -> None:
        """Subscribe to everything this bridge writes the log from."""
        self._live = live
        for name in LISTENED:
            live.on(name, self._heard)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`

    def stop(self) -> None:
        """Let go of the session: the call is over."""
        live = self._live
        if live is None:
            return
        for name in LISTENED:
            live.off(name, self._heard)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        self._live = None

    # One callback for every event, dispatched on the library's own discriminator: a table beats
    # six almost-identical closures, and a new event type is one row.
    def _heard(self, event: Any) -> None:
        """One session event, as the entries it becomes."""
        handler = _HANDLERS.get(getattr(event, "type", ""))
        if handler is not None:
            handler(self, event)

    # ── the caller ──────────────────────────────────────────────────────────────

    def transcribed(self, event: session_events.UserInputTranscribedEvent) -> None:
        """user.transcript: what the recogniser hears, interim and final. Interim is ephemeral."""
        self._language = event.language or self._language
        said = UserTranscript(
            text=event.transcript,
            final=event.is_final,
            language=event.language,
        )
        self._writing.later("user.transcript", said, ephemeral=not event.is_final)

    def user_state(self, event: session_events.UserStateChangedEvent) -> None:
        """user.state: the platform's belief about what the person on the line is doing."""
        self._writing.later("user.state", UserStateChanged(state=event.new_state))

    def agent_state(self, event: session_events.AgentStateChangedEvent) -> None:
        """agent.state: warming up, waiting, hearing, generating, or playing audio."""
        self._writing.later("agent.state", AgentStateChanged(state=event.new_state))

    # ── the turns ───────────────────────────────────────────────────────────────

    # A message reaches conversation_item_added with its metrics already complete
    # (agent_session.py:2065), which is why the turn is written here and nowhere earlier.
    def item_added(self, event: session_events.ConversationItemAddedEvent) -> None:
        """turn.user or turn.agent, with everything the session measured about it."""
        item = event.item
        if not isinstance(item, agents.ChatMessage):
            return
        speech = _a_speech(self._live)
        if item.role == "user":
            self._a_user_turn(item, speech)
        elif item.role == "assistant":
            self._an_agent_turn(item, speech)

    def _a_user_turn(self, item: agents.ChatMessage, speech: str | None) -> None:
        """The caller's turn, and the end-of-utterance block its own report carries."""
        self.turns += 1
        said: dict[str, Any] = {
            "speech_id": speech or item.id,
            "item_id": item.id,
            "text": item.text_content or "",
            "metrics": wire.UserTurnMetrics.model_validate(dict(item.metrics)),
        }
        if self._language is not None:
            said["language"] = self._language
        if item.transcript_confidence is not None:
            said["transcript_confidence"] = item.transcript_confidence
        detector = None if self._live is None else self._live.turn_detection
        eou = an_end_of_utterance(item.metrics, speech, detector)
        if eou is not None:
            self._writing.later("metrics.eou", wire.EOUMetrics.model_validate(eou.model_dump()))
        self._writing.later("turn.user", UserTurnEnded.model_validate(said))

    def _an_agent_turn(self, item: agents.ChatMessage, speech: str | None) -> None:
        """The agent's reply, whether it finished or the caller cut across it."""
        self.last_said = item.text_content or self.last_said
        self._writing.later(
            "turn.agent",
            AgentTurnEnded(
                speech_id=speech or item.id,
                item_id=item.id,
                text=item.text_content or "",
                interrupted=item.interrupted,
                metrics=wire.AgentTurnMetrics.model_validate(dict(item.metrics)),
            ),
        )

    # ── the words as they play, and what the call spent ─────────────────────────

    # An aligned transcript arrives one word at a time, each a TimedString carrying the seconds
    # ElevenLabs measured for it (agent_activity.py:120-140, elevenlabs/tts.py:1332-1360). That is
    # the ONE source of word timings: the words the caller actually heard, in the order the audio
    # played them, so an interrupted reply times exactly what was spoken and nothing after it.
    def said(self, delta: str | TimedString) -> None:
        """agent.transcript: one delta of the reply, with the word timings the voice aligned."""
        said: dict[str, Any] = {
            "speech_id": _a_speech(self._live) or "",
            "text": str(delta),
            "final": False,
        }
        said.update(_word_timings(delta))
        self._writing.later("agent.transcript", AgentTranscript.model_validate(said))

    def used(self, event: session_events.SessionUsageUpdatedEvent) -> None:
        """session_usage_updated: the rows the summary will carry, kept as livekit sums them."""
        self._meters.collected_usage(event.usage)

    # livekit retries a component error and closes the session only after three unrecoverable ones
    # (agent_session.py:1830-1844), which is right for a vendor having a bad minute and wrong for a
    # vendor refusing the request itself: a wrong voice or a wrong key answers the same way forever,
    # and the caller spends that time listening to an apology. One entry, and the call ends.
    def failed(self, event: session_events.ErrorEvent) -> None:
        """A component said no. What it said is the log's, verbatim: it is what gets debugged."""
        if self._dead_end:
            return
        said = str(event.error)
        # The vendor's own exception, under livekit's report of it: the report says whether the
        # library will retry, the exception says whether anything could come of it.
        failed_with: object = getattr(event.error, "error", None)
        if not (isinstance(failed_with, BaseException) and is_a_dead_end(failed_with)):
            self._writing.later(
                "error",
                ErrorEvent(
                    code=COMPONENT_FAILED,
                    message=said,
                    recoverable=getattr(event.error, "recoverable", False),
                ),
            )
            return
        self._dead_end = True
        self._writing.later(
            "error", ErrorEvent(code=COMPONENT_DEAD_END, message=said, recoverable=False)
        )
        self._ending.ends_for(f"{getattr(event.error, 'type', 'error')}: {said}")


# livekit reports a timing it does not have as NOT_GIVEN, never as None, so an unaligned voice
# leaves both keys off the entry rather than writing two nulls into every word of every reply.
def _word_timings(delta: str | TimedString) -> dict[str, float]:
    """The seconds the voice measured for these words, or nothing at all when it measured none."""
    if not isinstance(delta, TimedString):
        return {}
    timed: dict[str, float] = {}
    if is_given(delta.start_time):
        timed["start"] = delta.start_time
    if is_given(delta.end_time):
        timed["end"] = delta.end_time
    return timed


# The speech the reply in flight belongs to, which is what joins a turn to its metrics blocks.
def _a_speech(live: AgentSession[None] | None) -> str | None:
    """The id of the speech running right now, or None between two of them."""
    if live is None:
        return None
    speech = live.current_speech
    return None if speech is None else speech.id


_HANDLERS: dict[str, Any] = {
    "user_input_transcribed": Events.transcribed,
    "user_state_changed": Events.user_state,
    "agent_state_changed": Events.agent_state,
    "conversation_item_added": Events.item_added,
    "session_usage_updated": Events.used,
    "error": Events.failed,
}
