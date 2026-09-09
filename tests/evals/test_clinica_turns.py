"""Ring 1 and ring 2 on Clínica Norte: what one turn says, and what it asks a tool for."""

# livekit's `AgentSession.run` and its RunResult are untyped where a test reads them; every ignore
# below is that one fact, said once.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from pinecall.evals import Answers, a_headless_call, a_judge
from pinecall.providers.models import Chat
from tests.evals.clinica import declared, prompt_at

pytestmark = [pytest.mark.evals, pytest.mark.needs_llm]

# The state the tenant's own capture calls 0: stage `identify`, nobody found, no hours anywhere.
# Every test below starts there, because that is the only state whose view is a rule and not a
# list — "Saluda y pide nombre y teléfono. Nada más hasta identificar al paciente."
IDENTIFY = 0

# What the agenda answers when the name and the phone do line up. The shape is the tenant's
# `Patient` (the clinic example's agenda, in the agents repository); the eval stands in for the
# app, not for the class, so it answers what the class would have returned and nothing more.
ANA = {
    "id": "p1",
    "name": "Ana García",
    "phone": "+34 600 000 001",
    "cita": "jueves a las diez",
    "doctor": "la doctora Vidal",
}


@pytest.fixture
async def judge() -> AsyncIterator[Chat]:
    """One Haiku for the whole test, closed after it: a judged assertion is one round trip."""
    model = a_judge()
    try:
        yield model
    finally:
        await model.aclose()


async def test_the_first_turn_greets_asks_who_is_calling_and_calls_nothing(judge: Chat) -> None:
    async with a_headless_call(
        declared(),
        prompt=prompt_at(IDENTIFY),
        answers=Answers({}),
    ) as call:
        result = await call.session.run(user_input="Hola, buenas. Quería pedir una cita.")

        await (
            result.expect.next_event()
            .is_message(role="assistant")
            .judge(
                judge,
                intent=(
                    "Greets the caller and begins identifying them by asking for their "
                    "name. It claims to have looked nothing up, and it names no date, no "
                    "time and no doctor."
                ),
            )
        )
        # Nothing else happened: no tool was called on the way to that sentence.
        result.expect.no_more_events()


async def test_one_utterance_with_both_reaches_find_patient_with_both(judge: Chat) -> None:
    answers = Answers({"findPatient": ANA})
    async with a_headless_call(
        declared(),
        prompt=prompt_at(IDENTIFY),
        answers=answers,
    ) as call:
        result = await call.session.run(
            user_input="Buenas, soy Ana García y mi teléfono es el 600 000 001."
        )

        asked = result.expect.contains_function_call(
            name="findPatient", arguments={"name": "Ana García"}
        )
        # The phone is dictated, so its spacing is the model's; the digits are the caller's.
        assert _digits(json.loads(asked.event().item.arguments)["phone"]) == "600000001"
        await (
            result.expect[-1]
            .is_message(role="assistant")
            .judge(
                judge,
                intent=(
                    "Tells the caller it has found their record. It invents no appointment "
                    "date, time or doctor that it was not given."
                ),
            )
        )


async def test_it_books_nothing_before_the_caller_is_identified(judge: Chat) -> None:
    answers = Answers({"findPatient": None})
    async with a_headless_call(
        declared(),
        prompt=prompt_at(IDENTIFY),
        answers=answers,
    ) as call:
        result = await call.session.run(
            user_input="Resérveme el martes a las cuatro con la doctora Vidal."
        )

        # `book` is declared and its schema is on the wire; what stops it is the prompt alone.
        assert answers.called("book") == ()
        await (
            result.expect[-1]
            .is_message(role="assistant")
            .judge(
                judge,
                intent=(
                    "It does not tell the caller that the appointment is already booked, "
                    "reserved or confirmed; a promise to book it once they are identified "
                    "is fine. It asks the caller to identify themselves first."
                ),
            )
        )


def _digits(dictated: str) -> str:
    """A phone as the caller said it, with whatever spacing the model wrote, reduced to digits."""
    return "".join(character for character in dictated if character.isdigit())
