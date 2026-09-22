"""The six verbs on a scripted session: the entry first, then exactly what livekit was asked."""

from __future__ import annotations

from typing import Any, cast

import pytest
from livekit.agents import llm as agents

from pinecall.session.supervising import A_RELEASE
from pinecall.session.voice import commands
from pinecall.session.voice.supervising import Supervising
from pinecall.session.voice.writing import Writing
from pinecall_protocol import Command, ProtocolError, encode, verbs
from pinecall_protocol.commands import SupervisorVerb
from pinecall_protocol.defs import EndedBy, EndReason, Supervisor
from tests.session.voice.fakes import CALL, Recording, ScriptedSession

pytestmark = pytest.mark.unit

ANA = Supervisor(id="sup_ab12cd", name="Ana")
BRUNO = Supervisor(id="sup_ff00ff")


class ScriptedAgent:
    """livekit's Agent as a verb reaches it: the history it reads, and the history it is given."""

    def __init__(self) -> None:
        self.chat_ctx = agents.ChatContext.empty()
        self.updates = 0

    async def update_chat_ctx(self, chat_ctx: agents.ChatContext) -> None:
        """voice/agent.py:236: the whole history back, with whatever was appended to it."""
        self.chat_ctx = chat_ctx
        self.updates += 1


class Ended:
    """The Ending the end verb reaches: the reason, and whose doing the log will say it was."""

    def __init__(self) -> None:
        self.hangups: list[tuple[EndReason, EndedBy, bool]] = []

    async def hangup(
        self, reason: EndReason, by: EndedBy = "agent", *, at_once: bool = False
    ) -> None:
        self.hangups.append((reason, by, at_once))

    def transferred(self) -> None:
        raise AssertionError("no verb in this file transfers")


class Desk:
    """One call under supervision: the session, the agent, the log it writes and who ended it."""

    def __init__(self) -> None:
        self.session = ScriptedSession()
        self.agent = ScriptedAgent()
        self.recording = Recording()
        self.writing = Writing(self.recording, CALL)
        self.writing.open()
        self.ending = Ended()
        self.supervising = Supervising(
            cast(Any, self.session), cast(Any, self.agent), self.writing, self.ending
        )

    async def verb(self, by: Supervisor, said: verbs.Verb) -> Any:
        """One verb, applied, with the log flushed so a test reads what the gateway got."""
        outcome = await self.supervising.apply(SupervisorVerb(by=by, verb=said))
        await self.writing.flushed()
        return outcome

    @property
    def entries(self) -> list[str]:
        """The entry types this call's log received, in order."""
        return self.recording.types


# Async, because Writing.open() starts the task that drains the queue: without a running loop
# there is nothing to drain into and `flushed()` would wait for ever.
@pytest.fixture
async def desk() -> Desk:
    return Desk()


async def test_say_writes_who_asked_before_the_agent_says_a_word(desk: Desk) -> None:
    await desk.verb(ANA, verbs.SayVerb(verb="say", text="Le confirmo el turno de las diez."))
    assert desk.entries == ["supervisor.said"]
    written = desk.recording.of("supervisor.said")[0]
    assert written.data["by"]["id"] == ANA.id and written.data["by"]["name"] == "Ana"
    assert written.data["text"] == "Le confirmo el turno de las diez."
    assert desk.session.said == [("Le confirmo el turno de las diez.", True)]


async def test_a_whisper_lands_in_the_history_and_asks_for_the_next_turn(desk: Desk) -> None:
    await desk.verb(ANA, verbs.WhisperVerb(verb="whisper", text="Respondé en tres palabras."))
    assert desk.entries == ["supervisor.whispered"]
    assert desk.recording.of("supervisor.whispered")[0].data["text"] == "Respondé en tres palabras."
    assert _a_message(desk.agent.chat_ctx.items[-1]).role == "system"
    assert "Respondé en tres palabras." in _the_note(desk)
    assert desk.session.replied == [_the_note(desk)]


def _the_note(desk: Desk) -> str:
    """The system message the whisper appended, which is also what it asked the turn for."""
    return _a_message(desk.agent.chat_ctx.items[-1]).text_content or ""


def _a_message(item: agents.ChatItem) -> agents.ChatMessage:
    """One history item as the message it must be: a tool call here would be the test lying."""
    assert isinstance(item, agents.ChatMessage)
    return item


async def test_the_whisper_never_touches_the_static_prefix(desk: Desk) -> None:
    """The static blocks are what the provider caches: a note there would rebuild every call."""
    await desk.verb(ANA, verbs.WhisperVerb(verb="whisper", text="Ofrecele el turno de las once."))
    assert desk.agent.updates == 1
    assert [_a_message(item).role for item in desk.agent.chat_ctx.items] == ["system"]


async def test_a_takeover_cuts_the_sentence_and_leaves_the_agent_mute_and_deaf(desk: Desk) -> None:
    await desk.verb(ANA, verbs.TakeoverVerb(verb="takeover"))
    assert desk.entries == ["supervisor.took_over"]
    assert desk.recording.of("supervisor.took_over")[0].data["by"]["id"] == ANA.id
    assert desk.session.interruptions == 1
    assert desk.session.output.enabled is False
    assert desk.session.input.enabled is False
    assert desk.supervising.taken_by == ANA


async def test_a_takeover_with_nothing_to_interrupt_still_takes_the_line(desk: Desk) -> None:
    """A session that is not speaking raises on interrupt; the agent was already quiet."""
    desk.session.interruptible = False
    await desk.verb(ANA, verbs.TakeoverVerb(verb="takeover"))
    assert desk.supervising.taken_by == ANA and desk.session.output.enabled is False


async def test_a_second_takeover_is_refused_and_names_who_holds_the_line(desk: Desk) -> None:
    await desk.verb(ANA, verbs.TakeoverVerb(verb="takeover"))
    with pytest.raises(ProtocolError, match=ANA.id):
        await desk.verb(BRUNO, verbs.TakeoverVerb(verb="takeover"))
    assert desk.entries == ["supervisor.took_over"]
    assert desk.supervising.taken_by == ANA


async def test_a_whisper_while_a_human_holds_the_line_never_talks_over_them(desk: Desk) -> None:
    await desk.verb(ANA, verbs.TakeoverVerb(verb="takeover"))
    await desk.verb(ANA, verbs.WhisperVerb(verb="whisper", text="Cerrá con el precio."))
    assert desk.agent.updates == 1
    assert desk.session.replied == []


async def test_a_release_gives_the_ears_back_first_and_then_the_voice(desk: Desk) -> None:
    await desk.verb(ANA, verbs.TakeoverVerb(verb="takeover"))
    await desk.verb(ANA, verbs.ReleaseVerb(verb="release"))
    assert desk.entries == ["supervisor.took_over", "supervisor.released"]
    assert desk.session.input.enabled is True and desk.session.output.enabled is True
    assert desk.supervising.taken_by is None


async def test_the_release_note_never_claims_to_know_what_the_human_said(desk: Desk) -> None:
    await desk.verb(ANA, verbs.TakeoverVerb(verb="takeover"))
    await desk.verb(ANA, verbs.ReleaseVerb(verb="release"))
    assert desk.session.replied == [A_RELEASE]
    assert "did not hear" in A_RELEASE and "Do not guess" in A_RELEASE
    assert _the_note(desk) == A_RELEASE


async def test_a_release_nobody_asked_for_is_refused_and_writes_nothing(desk: Desk) -> None:
    with pytest.raises(ProtocolError, match="nobody holds the line"):
        await desk.verb(ANA, verbs.ReleaseVerb(verb="release"))
    assert desk.entries == []
    assert desk.session.replied == []


async def test_a_transfer_is_written_here_and_finished_by_the_transfer_applier(desk: Desk) -> None:
    """One transfer, one answer: this verb hands the command back rather than sending a second."""
    wanted = await desk.verb(
        ANA, verbs.TransferVerb(verb="transfer", to="+59891234567", mode="cold")
    )
    assert desk.entries == ["supervisor.transferred"]
    written = desk.recording.of("supervisor.transferred")[0]
    assert written.data == {
        "by": {"id": ANA.id, "name": "Ana"},
        "to": "+59891234567",
        "mode": "cold",
    }
    assert wanted is not None and (wanted.to, wanted.mode) == ("+59891234567", "cold")


async def test_the_end_verb_hangs_up_as_the_supervisor_and_never_as_the_agent(desk: Desk) -> None:
    await desk.verb(ANA, verbs.EndVerb(verb="end", reason="El cliente pidió hablar mañana."))
    assert desk.entries == ["supervisor.ended"]
    assert (
        desk.recording.of("supervisor.ended")[0].data["reason"] == "El cliente pidió hablar mañana."
    )
    # At once: Stop pressed mid-sentence, or while a slow model is still writing, must not wait for
    # either to finish — the desk pressed it twice and thrice while the call drained.
    assert desk.ending.hangups == [("supervisor_ended", "supervisor", True)]


async def test_a_supervise_verb_on_a_call_with_no_session_is_refused_by_name() -> None:
    """The applier reaches the desk through Applying, which holds none before the session opens."""
    nothing = cast(Any, None)
    applying = commands.Applying(cast(Any, ScriptedSession()), nothing, nothing, nothing)
    said = Command(
        id="cmd_1",
        type="supervisor.verb",
        agent="clinica-norte",
        call=CALL,
        data=encode(SupervisorVerb(by=ANA, verb=verbs.SayVerb(verb="say", text="hola"))),
    )
    with pytest.raises(ProtocolError, match="needs a live session"):
        await commands.apply(applying, said)


async def test_every_one_of_the_six_verbs_lands_in_the_callers_log(desk: Desk) -> None:
    """Six verbs, six entries, in order, each naming the supervisor the door said sent it."""
    for said in (
        verbs.SayVerb(verb="say", text="Ya se lo confirmo."),
        verbs.WhisperVerb(verb="whisper", text="Ofrecele las once."),
        verbs.TakeoverVerb(verb="takeover"),
        verbs.ReleaseVerb(verb="release"),
        verbs.TransferVerb(verb="transfer", to="+59891234567", mode="cold"),
        verbs.EndVerb(verb="end", reason="Quedó resuelto."),
    ):
        await desk.verb(ANA, said)
    assert desk.entries == [
        "supervisor.said",
        "supervisor.whispered",
        "supervisor.took_over",
        "supervisor.released",
        "supervisor.transferred",
        "supervisor.ended",
    ]
    assert all(one.data["by"] == {"id": ANA.id, "name": "Ana"} for one in desk.recording.entries)
    # The seq is the platform's to stamp, and it stamps it because these went down the one path
    # every other entry of this call takes: Writing, then the platform's append.
    assert len(desk.recording.entries) == 6
