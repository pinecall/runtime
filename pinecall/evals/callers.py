"""The simulated caller: a model playing a persona, one improvised line at a time."""

import json
from collections.abc import Mapping, Sequence
from typing import Never

from livekit.agents import llm
from livekit.agents.llm import ChatContext, ChatRole

from pinecall.domain.errors import UpstreamFailed
from pinecall.domain.names import JsonObject
from pinecall.providers.build import a_mapping
from pinecall.wire.frames import Entry
from pinecall.wire.rest.evals import CallerPersona, NextLineRequest, NextLineResponse, Spoken

# As LiveKit's simulations frame it: the persona is the system message, its facts a block in it.
A_CALLER = (
    "You are a person who has just phoned a business, and you answer as that person and nobody "
    "else. Say one short turn, the way somebody speaks on the phone: no narration, no stage "
    "directions, no quotation marks, never more than two sentences. Speak the language the "
    "business is speaking to you unless your own style says otherwise. Use the facts about "
    "yourself when they are asked for, and volunteer one when it moves you towards what you "
    "want. Never invent a fact about yourself that is not listed. Hang up once you have what you "
    "came for, or once it is clear you will not get it."
)


PERSONA = "What you want: {goal}\nHow you talk: {style}\nFacts about you:\n{facts}"


NO_FACTS = "  (you were given none: do not make any up)"


# The last turn is announced, so the caller says goodbye instead of being cut mid-sentence.
TURNS_LEFT = "You have {left} turn(s) left in this call."


THE_LAST_TURN = "This is your last turn: finish what you are saying and hang up."


NOTHING_SAID = "the model playing the caller answered without calling say_next_line"


# The line is read off the call's arguments; the tool itself never runs.
SAY_NEXT_LINE: JsonObject = {
    "name": "say_next_line",
    "description": "Say your next turn on the phone.",
    "parameters": {
        "type": "object",
        "properties": {
            "line": {
                "type": "string",
                "description": "What you say out loud, in one or two sentences.",
            },
            "hanging_up": {
                "type": "boolean",
                "description": "True when this is the last thing you will say on this call.",
            },
        },
        "required": ["line", "hanging_up"],
    },
}


SIDES: Mapping[str, str] = {"turn.agent": "agent", "turn.user": "caller"}


def heard_in(entries: Sequence[Entry]) -> list[Spoken]:
    """Both sides' turns of a call's log, in order, as the caller heard them."""
    return [
        Spoken.model_validate({"who": SIDES[entry.type], "said": str(entry.data.get("text", ""))})
        for entry in entries
        if entry.type in SIDES
    ]


# The caller's rule (accepts_when, declines_when) is the judge's alone: a caller that knew it
# would play to it.
async def improvise_line(model: llm.LLM[Never], request: NextLineRequest) -> NextLineResponse:
    """The caller's next turn: the persona, the call so far, and how many turns are left."""
    response = await model.chat(
        chat_ctx=_call_so_far(request),
        tools=[llm.function_tool(_never_run, raw_schema=SAY_NEXT_LINE)],
        tool_choice="required",
    ).collect()
    if not response.tool_calls:
        raise UpstreamFailed(NOTHING_SAID)
    answered: object = json.loads(response.tool_calls[0].arguments or "{}")
    fields: Mapping[str, object] = answered if a_mapping(answered) else {}
    line = str(fields.get("line", "")).strip()
    # An empty line is a caller who has hung up.
    return NextLineResponse(say=line, hangup=bool(fields.get("hanging_up", False)) or not line)


# The model is the caller: its turns are the assistant's, the business's are the user's.
def _call_so_far(request: NextLineRequest) -> ChatContext:
    context = ChatContext.empty()
    context.add_message(role="system", content=f"{A_CALLER}\n\n{_persona_of(request.persona)}")
    for turn in request.heard:
        role: ChatRole = "user" if turn.who == "agent" else "assistant"
        context.add_message(role=role, content=turn.said)
    left = request.turns_left
    context.add_message(
        role="system", content=THE_LAST_TURN if left <= 1 else TURNS_LEFT.format(left=left)
    )
    return context


async def _never_run(raw_arguments: dict[str, object]) -> str:
    return str(raw_arguments.get("line", ""))


def _persona_of(persona: CallerPersona) -> str:
    facts = "\n".join(f"  {name}: {value}" for name, value in persona.facts.items())
    return PERSONA.format(goal=persona.goal, style=persona.style, facts=facts or NO_FACTS)
