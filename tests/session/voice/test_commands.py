"""A protocol command onto a live session: what each verb does, and which are refused by name."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest
from livekit.agents.types import NOT_GIVEN

from pinecall.session.voice import commands
from pinecall_protocol import Command, ProtocolError, defs

pytestmark = pytest.mark.unit


class Session:
    """livekit's AgentSession, as far as the appliers reach it."""

    def __init__(self) -> None:
        self.said: list[tuple[str, Any]] = []
        self.replied: list[tuple[str, Any]] = []

    def say(self, text: str, *, allow_interruptions: Any = NOT_GIVEN) -> None:
        self.said.append((text, allow_interruptions))

    def generate_reply(self, *, instructions: str, allow_interruptions: Any = NOT_GIVEN) -> None:
        self.replied.append((instructions, allow_interruptions))


class Prompt:
    """The Prompting the appliers reach: what was set, in order."""

    def __init__(self) -> None:
        self.regions: list[tuple[str, str]] = []
        self.tools: list[list[str]] = []

    async def set_prompt(self, region: defs.PromptRegion, text: str) -> None:
        self.regions.append((region, text))

    async def set_tools(self, tools: Sequence[defs.ToolSpec]) -> None:
        self.tools.append([tool.name for tool in tools])


class End:
    """The Ending the appliers reach."""

    def __init__(self) -> None:
        self.reasons: list[str] = []
        self.transfers = 0

    async def hangup(self, reason: defs.EndReason) -> None:
        self.reasons.append(reason)

    def transferred(self) -> None:
        self.transfers += 1


class Recorded:
    """The Recording the appliers reach: the entries a command asked this call's log for."""

    def __init__(self) -> None:
        self.states: list[tuple[dict[str, Any], list[str]]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.lines: list[tuple[str, dict[str, Any]]] = []

    async def set_state(self, state: Mapping[str, Any], changed: Sequence[str]) -> None:
        """state.set: the whole state, and what moved in it."""
        self.states.append((dict(state), list(changed)))

    async def receives(self, name: str, data: Mapping[str, Any]) -> None:
        """call.event: a fact from the tenant's backend."""
        self.events.append((name, dict(data)))

    async def log_custom(self, name: str, data: Mapping[str, Any]) -> None:
        """call.log: a line of the app's own."""
        self.lines.append((name, dict(data)))


def a_command(type: str, data: dict[str, Any]) -> Command:
    return Command(type=type, agent="clinica-norte", call="call_1", data=data)


@pytest.fixture
def applying() -> tuple[commands.Applying, Session, Prompt, End]:
    live, prompt, end = Session(), Prompt(), End()
    return commands.Applying(live, prompt, end, Recorded()), live, prompt, end  # pyright: ignore[reportArgumentType]


async def test_agent_say_is_the_sessions_say_verbatim(
    applying: tuple[commands.Applying, Session, Prompt, End],
) -> None:
    apply, live, _prompt, _end = applying
    await commands.apply(apply, a_command("agent.say", {"text": "Un momento."}))
    await commands.apply(
        apply, a_command("agent.say", {"text": "Escuche.", "allow_interruptions": False})
    )
    assert live.said == [("Un momento.", NOT_GIVEN), ("Escuche.", False)]


async def test_agent_reply_is_one_model_turn_guided_by_the_instruction(
    applying: tuple[commands.Applying, Session, Prompt, End],
) -> None:
    apply, live, _prompt, _end = applying
    await commands.apply(apply, a_command("agent.reply", {"instructions": "offer the 10:15"}))
    assert live.replied == [("offer the 10:15", NOT_GIVEN)]


async def test_prompt_set_and_tools_set_reach_the_prompt_and_never_the_session(
    applying: tuple[commands.Applying, Session, Prompt, End],
) -> None:
    apply, live, prompt, _end = applying
    await commands.apply(
        apply, a_command("prompt.set", {"region": "view", "text": "Ana is calling"})
    )
    book = {"name": "book", "description": "Book", "parameters": {"type": "object"}}
    await commands.apply(apply, a_command("tools.set", {"tools": [book]}))
    assert prompt.regions == [("view", "Ana is calling")]
    assert prompt.tools == [["book"]]
    assert live.said == [] and live.replied == []


async def test_call_hangup_ends_the_call_as_the_agent(
    applying: tuple[commands.Applying, Session, Prompt, End],
) -> None:
    apply, _live, _prompt, end = applying
    await commands.apply(apply, a_command("call.hangup", {}))
    assert end.reasons == ["agent_hung_up"]


@pytest.mark.parametrize("type", ["call.dtmf", "call.hold"])
async def test_a_verb_on_the_callers_sip_leg_is_refused_by_name_and_not_dropped(
    applying: tuple[commands.Applying, Session, Prompt, End], type: str
) -> None:
    apply, _live, _prompt, _end = applying
    with pytest.raises(ProtocolError, match=type):
        await commands.apply(
            apply,
            a_command(type, {"to": "+59891111", "mode": "cold", "digits": "1"}),
        )


# A command whose whole effect is a line in the log travels to the worker like every other one:
# the process running the call is the process that writes its log.
async def test_the_state_the_app_set_lands_in_this_calls_log_whole(
    applying: tuple[commands.Applying, Session, Prompt, End],
) -> None:
    apply, _live, _prompt, _end = applying
    await commands.apply(apply, a_command("state.set", {"state": {"patient": "Ana", "slot": None}}))
    await commands.apply(
        apply, a_command("state.set", {"state": {"patient": "Ana"}, "changed": ["patient"]})
    )
    assert _recorded(apply).states == [
        ({"patient": "Ana", "slot": None}, ["patient", "slot"]),
        ({"patient": "Ana"}, ["patient"]),
    ]


async def test_call_event_and_call_log_are_written_where_the_call_runs(
    applying: tuple[commands.Applying, Session, Prompt, End],
) -> None:
    apply, _live, _prompt, _end = applying
    await commands.apply(apply, a_command("call.event", {"name": "paid", "data": {"cents": 900}}))
    await commands.apply(apply, a_command("call.log", {"name": "state.cause", "data": {"f": "x"}}))
    assert _recorded(apply).events == [("paid", {"cents": 900})]
    assert _recorded(apply).lines == [("state.cause", {"f": "x"})]


async def test_a_command_whose_data_is_not_its_shape_is_refused(
    applying: tuple[commands.Applying, Session, Prompt, End],
) -> None:
    apply, _live, _prompt, _end = applying
    with pytest.raises(ProtocolError):
        await commands.apply(apply, a_command("agent.say", {"instructions": "wrong shape"}))


def _recorded(applying: commands.Applying) -> Recorded:
    """The Recording this applying was built with, as the test that built it knows it."""
    return cast(Recorded, applying.recording)
