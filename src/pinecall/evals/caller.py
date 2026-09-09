"""The person on the other end of a simulated call: a model improvising towards its own goal."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from livekit.agents.llm import ChatContext, ChatRole, function_tool
from pydantic import Field

from pinecall.log.entry import Entry
from pinecall.providers.models import Chat
from pinecall_protocol import WireModel

# LiveKit's own simulations hand the synthetic caller a persona as `instructions` and keep the
# facts it must not invent in `userdata` (docs agents/start/testing/simulations). Both ideas are
# taken here — the persona is the system message, the facts are a block inside it — and the third,
# `agent_expectations`, is our goldens' `expect`. What is NOT taken is where it runs: their
# simulations run on LiveKit Cloud. See docs/decisions/simulate.md.
A_CALLER = (
    "You are a person who has just phoned a business, and you answer as that person and nobody "
    "else. Say one short turn, the way somebody speaks on the phone: no narration, no stage "
    "directions, no quotation marks, never more than two sentences. Speak the language the "
    "business is speaking to you unless your own style says otherwise. Use the facts about "
    "yourself when they are asked for, and volunteer one when it moves you towards what you "
    "want. Never invent a fact about yourself that is not listed. Hang up once you have what you "
    "came for, or once it is clear you will not get it."
)

# What the model is told about itself, in the order a person would say it.
PERSONA = "What you want: {goal}\nHow you talk: {style}\nFacts about you:\n{facts}"

NO_FACTS = "  (you were given none: do not make any up)"

# The last turn is announced so the caller can close rather than be cut off mid-sentence. A caller
# who does not know the call is ending says goodbye in the middle of a booking.
TURNS_LEFT = "You have {left} turn(s) left in this call."
THE_LAST_TURN = "This is your last turn: finish what you are saying and hang up."

# Who said what, from the caller's side of the line: the business is the one talking TO them.
THE_BUSINESS = "agent"
THE_CALLER = "caller"

# The two entries a conversation is made of, and the one key both carry their words under.
TURNS: dict[str, str] = {"turn.agent": THE_BUSINESS, "turn.user": THE_CALLER}
SAID = "text"

NOTHING_SAID = "the caller model answered without calling say_next_line"

# Both doors that play a caller reach for the same model and refuse in the same sentence.
NO_MODEL = "no model to play the caller with: {missing}"


class Persona(WireModel):
    """A synthetic caller as the tenant wrote it: what they want, how they talk, what they know."""

    name: str = ""
    goal: str
    style: str
    # Deterministic: a name, a phone, the appointment they are calling about. The model is told to
    # use these and to invent nothing beside them, which is what makes a simulated call repeatable
    # in the only way an improvised one can be.
    facts: dict[str, Any] = Field(default_factory=dict[str, Any])


class Spoken(WireModel):
    """One turn of the call as the caller's side remembers it: who said it, and what was said."""

    who: str
    said: str


class Improvised(WireModel):
    """What the caller says next, and whether they are done talking to this business."""

    say: str
    hangup: bool = False


class Asking(WireModel):
    """What the CLI puts on this door: the caller, the call so far, and how much line is left."""

    persona: Persona
    heard: list[Spoken] = Field(default_factory=list[Spoken])
    turns_left: int = 1


def heard_in(entries: Sequence[Entry]) -> list[Spoken]:
    """The conversation so far, off the call's own log: both sides, in the order they were said."""
    return [
        Spoken(who=TURNS[entry.type], said=str(entry.data.get(SAID, "")))
        for entry in entries
        if entry.type in TURNS
    ]


async def what_they_say_next(llm: Chat, asking: Asking) -> Improvised:
    """One turn improvised by the model that is playing the caller, in and out in one call."""

    # The body never runs: the line is read off the arguments the model sent, the way livekit's
    # own judge reads its verdict (evals/judge.py:155-161). The schema is what this declares.
    @function_tool
    async def say_next_line(line: str, hanging_up: bool) -> str:  # noqa: ARG001
        """Say your next turn on the phone.

        Args:
            line: What you say out loud, in one or two sentences.
            hanging_up: True when this is the last thing you will say on this call.
        """
        return line

    response = await llm.chat(
        chat_ctx=_the_call_so_far(asking),
        tools=[say_next_line],
        tool_choice="required",
    ).collect()
    if not response.tool_calls:
        raise ValueError(NOTHING_SAID)
    return _read(json.loads(response.tool_calls[0].arguments))


def _the_call_so_far(asking: Asking) -> ChatContext:
    """The persona as the system message, then the call with the roles the caller's side sees."""
    context = ChatContext.empty()
    context.add_message(role="system", content=f"{A_CALLER}\n\n{_persona_of(asking.persona)}")
    # The model IS the caller, so its own past turns are the assistant's and the business's are
    # the user's. Handing it the transcript as prose instead would ask it to work out which lines
    # were its own before it could say the next one.
    for turn in asking.heard:
        role: ChatRole = "user" if turn.who == THE_BUSINESS else "assistant"
        context.add_message(role=role, content=turn.said)
    context.add_message(role="system", content=_how_much_line_is_left(asking.turns_left))
    return context


def _persona_of(persona: Persona) -> str:
    """The three things the caller knows about themselves, as the system message carries them."""
    facts = "\n".join(f"  {name}: {value}" for name, value in persona.facts.items())
    return PERSONA.format(goal=persona.goal, style=persona.style, facts=facts or NO_FACTS)


def _how_much_line_is_left(turns_left: int) -> str:
    """How many turns the caller has, so the last one is a goodbye rather than a cut sentence."""
    return THE_LAST_TURN if turns_left <= 1 else TURNS_LEFT.format(left=turns_left)


def _read(answered: dict[str, Any]) -> Improvised:
    """The submitted arguments as a turn. A caller that said nothing at all has hung up."""
    line = str(answered.get("line", "")).strip()
    return Improvised(say=line, hangup=bool(answered.get("hanging_up", False)) or not line)
