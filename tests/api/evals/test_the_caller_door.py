"""POST /v1/evals/caller: the persona reaches the model, and one line comes back."""

import httpx
import pytest

from tests.session.fake_llm import FakeLLM, Scripted, a_call

pytestmark = pytest.mark.unit

DOOR = "/v1/evals/caller"

APURADO = {
    "name": "apurado",
    "goal": "cambiar la cita al martes por la tarde",
    "style": "frases cortas, interrumpe",
    "facts": {"name": "Ana García", "phone": "+34 600 000 001"},
}


def said(line: str, *, hanging_up: bool = False) -> Scripted:
    """The caller model answering the way the door's tool schema asks it to."""
    said = {"line": line, "hanging_up": hanging_up}
    return Scripted(calls=(a_call("call_1", "say_next_line", said),))


async def test_the_line_the_model_said_comes_back_with_its_hangup(
    suite_http: httpx.AsyncClient, llm: FakeLLM
) -> None:
    """One turn in, one turn out: the door invents nothing the model did not say."""
    llm.script = [said("el martes por la tarde, la de las cuatro", hanging_up=True)]

    answered = await suite_http.post(DOOR, json={"persona": APURADO, "turns_left": 3})

    assert answered.status_code == 200
    assert answered.json() == {"say": "el martes por la tarde, la de las cuatro", "hangup": True}


async def test_the_persona_and_its_facts_are_what_the_model_is_told_it_is(
    suite_http: httpx.AsyncClient, llm: FakeLLM
) -> None:
    """The goal, the style and every fact reach the model, which is what makes them usable."""
    llm.script = [said("hola")]

    await suite_http.post(DOOR, json={"persona": APURADO, "turns_left": 3})

    system = llm.asked[0].system
    assert "cambiar la cita al martes por la tarde" in system
    assert "frases cortas, interrumpe" in system
    assert "Ana García" in system
    assert "+34 600 000 001" in system


async def test_the_call_so_far_arrives_with_the_business_as_the_one_talking_to_the_caller(
    suite_http: httpx.AsyncClient, llm: FakeLLM
) -> None:
    """The model IS the caller, so its own turns are the assistant's and the clinic's the user's."""
    llm.script = [said("la de las cuatro")]

    await suite_http.post(
        DOOR,
        json={
            "persona": APURADO,
            "heard": [
                {"who": "agent", "said": "Clínica Norte, ¿en qué puedo ayudarle?"},
                {"who": "caller", "said": "quiero cambiar mi cita"},
                {"who": "agent", "said": "¿Para qué día?"},
            ],
            "turns_left": 2,
        },
    )

    history = [(message.role, message.text_content) for message in llm.asked[0].history]
    assert history == [
        ("user", "Clínica Norte, ¿en qué puedo ayudarle?"),
        ("assistant", "quiero cambiar mi cita"),
        ("user", "¿Para qué día?"),
    ]


async def test_the_last_turn_is_announced_so_the_caller_says_goodbye_instead_of_being_cut(
    suite_http: httpx.AsyncClient, llm: FakeLLM
) -> None:
    """A caller who does not know the line is ending finishes in the middle of a booking."""
    llm.script = [said("gracias, hasta luego", hanging_up=True)]

    await suite_http.post(DOOR, json={"persona": APURADO, "turns_left": 1})

    assert "This is your last turn" in llm.asked[0].system


async def test_a_caller_who_said_nothing_at_all_has_hung_up(
    suite_http: httpx.AsyncClient, llm: FakeLLM
) -> None:
    """An empty line is not a turn: the call ends rather than a blank one going on the wire."""
    llm.script = [said("   ")]

    answered = await suite_http.post(DOOR, json={"persona": APURADO, "turns_left": 3})

    assert answered.json() == {"say": "", "hangup": True}


async def test_a_model_that_called_nothing_is_a_502_and_not_a_line_nobody_said(
    suite_http: httpx.AsyncClient, llm: FakeLLM
) -> None:
    """The line is read off the tool call; with no tool call there is no turn to report."""
    llm.script = [Scripted(chunks=("no pienso llamar a la herramienta",))]

    answered = await suite_http.post(DOOR, json={"persona": APURADO, "turns_left": 3})

    assert answered.status_code == 502
    assert "say_next_line" in answered.json()["detail"]
