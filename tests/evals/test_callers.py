"""Tests for the simulated caller: a model playing a persona, one line at a time."""

import pytest
from livekit.agents.llm import ChatMessage

from pinecall.domain.errors import UpstreamFailed
from pinecall.domain.names import JsonObject
from pinecall.evals.callers import Spending, heard_in, improvise_line
from pinecall.providers.catalog import Providers
from pinecall.wire.rest.evals import NextLineRequest
from tests.conftest import configured
from tests.evals.conftest import a_log, agent, caller, entry
from tests.fakes.acme import AcmeLLM, AcmeTTS

APURADO: JsonObject = {
    "name": "apurado",
    "goal": "cambiar la cita al martes por la tarde",
    "style": "frases cortas, interrumpe",
    "facts": {"name": "Ana García", "phone": "+34 600 000 001"},
}


def playing(line: str, *, hanging_up: bool = False) -> AcmeLLM:
    """A caller model that says one line."""
    next_line: dict[str, object] = {
        "name": "say_next_line",
        "arguments": {"line": line, "hanging_up": hanging_up},
    }
    return AcmeLLM(api_key="k", replies=[[next_line]])


def request_of(**written: object) -> NextLineRequest:
    return NextLineRequest.model_validate({"persona": APURADO, "turns_left": 3, **written})


def system_of(model: AcmeLLM) -> str:
    return "\n".join(
        item.text_content or ""
        for item in model.requests[0].items
        if isinstance(item, ChatMessage) and item.role == "system"
    )


async def test_the_line_the_model_said_comes_back_with_its_hangup() -> None:
    model = playing("el martes por la tarde, la de las cuatro", hanging_up=True)
    answer = (await improvise_line(model, request_of())).answer
    assert answer.written() == {"say": "el martes por la tarde, la de las cuatro", "hangup": True}


async def test_the_persona_and_its_facts_are_what_the_model_is_told_it_is() -> None:
    model = playing("hola")
    await improvise_line(model, request_of())
    system = system_of(model)
    assert "cambiar la cita al martes por la tarde" in system
    assert "frases cortas, interrumpe" in system
    assert "Ana García" in system
    assert "+34 600 000 001" in system


async def test_a_persona_with_no_facts_is_told_to_make_none_up() -> None:
    model = playing("hola")
    await improvise_line(model, request_of(persona={**APURADO, "facts": {}}))
    assert "do not make any up" in system_of(model)


async def test_the_call_so_far_arrives_with_the_business_as_the_one_talking_to_the_caller() -> None:
    model = playing("la de las cuatro")
    heard = [
        {"who": "agent", "said": "Clínica Norte, ¿en qué puedo ayudarle?"},
        {"who": "caller", "said": "quiero cambiar mi cita"},
        {"who": "agent", "said": "¿Para qué día?"},
    ]
    await improvise_line(model, request_of(heard=heard, turns_left=2))
    history = [
        (item.role, item.text_content)
        for item in model.requests[0].items
        if isinstance(item, ChatMessage) and item.role != "system"
    ]
    assert history == [
        ("user", "Clínica Norte, ¿en qué puedo ayudarle?"),
        ("assistant", "quiero cambiar mi cita"),
        ("user", "¿Para qué día?"),
    ]


async def test_the_last_turn_is_announced_so_the_caller_says_goodbye_instead_of_being_cut() -> None:
    model = playing("gracias, hasta luego", hanging_up=True)
    await improvise_line(model, request_of(turns_left=1))
    assert "This is your last turn" in system_of(model)


async def test_a_caller_who_said_nothing_at_all_has_hung_up() -> None:
    answer = (await improvise_line(playing("   "), request_of())).answer
    assert answer.written() == {"say": "", "hangup": True}


# The rule is the judge's: a caller that saw it would play to it.
async def test_the_callers_own_rule_is_never_told_to_the_model_playing_it() -> None:
    model = playing("hola")
    ruled = {**APURADO, "accepts_when": "SECRET-ACCEPT", "declines_when": "SECRET-DECLINE"}
    await improvise_line(model, request_of(persona=ruled))
    assert "SECRET" not in str(
        [item.text_content for item in model.requests[0].items if isinstance(item, ChatMessage)]
    )


async def test_a_model_that_called_nothing_is_a_vendor_failure_and_not_a_line_nobody_said() -> None:
    model = AcmeLLM(api_key="k", replies=[["no pienso llamar a la herramienta"]])
    with pytest.raises(UpstreamFailed, match="say_next_line"):
        await improvise_line(model, request_of())


def test_both_sides_of_a_log_are_read_in_order_as_the_caller_heard_them() -> None:
    log = [*a_log(caller("hola"), agent("buenas")), entry(3, "tool.call", {})]
    assert [(spoken.who, spoken.said) for spoken in heard_in(log)] == [
        ("caller", "hola"),
        ("agent", "buenas"),
    ]


def priced(ceiling_usd: float | None) -> Providers:
    """The box's row, its voice priced by the character, with the caller's ceiling when given."""
    row = configured().model_dump(mode="json")
    row["rates"]["acme-voice"] = {"characters": 0.001}
    if ceiling_usd is not None:
        row["caller"] = {"ceiling_usd": ceiling_usd}
    return Providers.model_validate(row)


async def test_a_callers_lines_and_voice_are_priced_and_it_stops_at_the_ceiling() -> None:
    spending = Spending(priced(0.02))
    line = await improvise_line(playing("diez letras"), request_of())
    assert not spending.is_over
    spending.count(line, AcmeTTS(speech_key="k"))
    # 20 tokens in at $1 and 5 out at $2 a million; 11 characters at $0.001.
    assert spending.usd == pytest.approx(0.00003 + 0.011)
    assert not spending.is_over
    spending.count(line, AcmeTTS(speech_key="k"))
    assert spending.is_over


async def test_a_row_with_no_caller_ceiling_never_stops_a_caller() -> None:
    spending = Spending(priced(None))
    line = await improvise_line(playing("una línea bastante larga " * 40), request_of())
    for _ in range(5):
        spending.count(line, AcmeTTS(speech_key="k"))
    assert spending.usd > 1
    assert not spending.is_over
