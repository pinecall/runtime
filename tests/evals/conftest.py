"""What the eval tests share: finished calls written by hand, a clinic's declaration, a judge."""

from pinecall.domain.agent import AgentConfig, ToolSpec
from pinecall.domain.names import JsonObject
from pinecall.evals.case import Arrived, Called, Case, Said
from pinecall.wire.frames import Entry
from tests.fakes.acme import AcmeLLM

AGENT = "clinica-norte"


CALL = "CA_8f4a2c"


BOOK = ToolSpec(
    name="book_appointment",
    description="Reserva la cita y se la lee al paciente.",
    parameters={"type": "object", "properties": {"slot": {"type": "string"}}},
    side_effect="irreversible",
    confirm="Le reservo el {slot}. ¿Lo confirmo?",
)


LOOK_UP = ToolSpec(
    name="find_slots",
    description="Busca los huecos libres de un día.",
    parameters={"type": "object", "properties": {"day": {"type": "string"}}},
)


# A call.summary as the seal writes it.
A_SUMMARY: JsonObject = {
    "reason": "caller_hung_up",
    "outcome": "booked",
    "duration_s": 42.0,
    "turns": 2,
    "usage": [],
    "cost": {
        "usd": 0.01,
        "rows": [],
        "unpriced": [],
    },
}


THE_CLINIC = AgentConfig(slug=AGENT, tools=(BOOK, LOOK_UP))


# The same tool, declared as one that changes nothing.
ONLY_LOOKING = AgentConfig(
    slug=AGENT,
    tools=(
        ToolSpec(name=BOOK.name, description=BOOK.description, parameters=BOOK.parameters),
        LOOK_UP,
    ),
)


def entry(seq: int, kind: str, data: JsonObject, *, ts: float | None = None) -> Entry:
    """One stored line of the clinic's call."""
    return Entry(
        seq=seq,
        ts=float(seq) if ts is None else ts,
        call=CALL,
        agent=AGENT,
        type=kind,
        ephemeral=False,
        data=data,
    )


def a_log(*lines: tuple[str, JsonObject]) -> list[Entry]:
    """A call's log, its seqs counted from 1."""
    return [entry(seq, kind, data) for seq, (kind, data) in enumerate(lines, 1)]


def caller(text: str, speech: str = "sp_1") -> tuple[str, JsonObject]:
    """The caller's turn."""
    return ("turn.user", {"speech_id": speech, "text": text, "metrics": {}})


def agent(text: str, speech: str = "sp_1", **measured: float) -> tuple[str, JsonObject]:
    """The agent's turn, with the measures livekit took of it."""
    turn: JsonObject = {"speech_id": speech, "text": text, "interrupted": False}
    turn["metrics"] = dict(measured)
    return ("turn.agent", turn)


def tool(
    name: str, arguments: JsonObject, *, call_id: str, speech: str = "sp_1"
) -> tuple[str, JsonObject]:
    """The model calling a tool."""
    return (
        "tool.call",
        {"call_id": call_id, "name": name, "arguments": arguments, "speech_id": speech},
    )


def result(name: str, output: JsonObject | str, *, call_id: str) -> tuple[str, JsonObject]:
    """What the app's tool answered."""
    return ("tool.result", {"call_id": call_id, "name": name, "output": output})


def confirmation(kind: str, *, call_id: str, audience: str = "caller") -> tuple[str, JsonObject]:
    """A confirm.request, .granted or .declined of the booking."""
    data: JsonObject = {"tool": BOOK.name, "call_id": call_id, "audience": audience}
    if kind == "confirm.request":
        data |= {"arguments": {}, "phrase": "¿Lo confirmo?", "ttl_s": 30}
    if kind == "confirm.granted":
        data |= {"said": "Sí, confirmo.", "ttl_s": 30}
    if kind == "confirm.declined":
        data |= {"reason": "no"}
    return (kind, data)


def confirmed() -> list[Entry]:
    """Asked, granted, then booked."""
    return a_log(
        caller("Quiero el jueves a las diez"),
        agent(
            "Le reservo el jueves a las 10. ¿Lo confirmo?",
            e2e_latency=1.0,
            llm_node_ttft=0.4,
            tts_node_ttfb=0.2,
        ),
        confirmation("confirm.request", call_id="toolu_01"),
        caller("Sí", "sp_2"),
        confirmation("confirm.granted", call_id="toolu_01"),
        tool(BOOK.name, {"slot": "jueves 10:00"}, call_id="toolu_01", speech="sp_2"),
        result(BOOK.name, {"booking": "BK-5521"}, call_id="toolu_01"),
        agent(
            "Hecho, queda reservada.",
            "sp_2",
            e2e_latency=1.08,
            llm_node_ttft=0.4,
            tts_node_ttfb=0.2,
        ),
    )


def before_the_yes() -> list[Entry]:
    """The booking ran before the caller's yes."""
    return a_log(
        caller("Quiero el jueves a las diez"),
        agent("Ya te la reservo."),
        confirmation("confirm.request", call_id="toolu_02"),
        tool(BOOK.name, {"slot": "jueves 10:00"}, call_id="toolu_02"),
        result(BOOK.name, {"booking": "BK-5522"}, call_id="toolu_02"),
        confirmation("confirm.granted", call_id="toolu_02"),
    )


def with_no_gate() -> list[Entry]:
    """An irreversible tool ran and the log carries no confirmation at all."""
    return a_log(
        caller("Quiero el jueves a las diez"),
        agent("Un momento."),
        tool(BOOK.name, {"slot": "jueves 10:00"}, call_id="toolu_03"),
        result(BOOK.name, {"booking": "BK-5523"}, call_id="toolu_03"),
        agent("Reservado."),
    )


def caller_line(text: str, *, seq: int = 1) -> Said:
    """A caller's turn of a case written by hand."""
    return Said(role="user", text=text, seq=seq)


def agent_line(
    text: str, *, seq: int = 2, calls: tuple[Called, ...] = (), retrieved: tuple[str, ...] = ()
) -> Said:
    """An agent's turn of a case written by hand."""
    return Said(role="assistant", text=text, seq=seq, calls=calls, retrieved=retrieved)


def logged_call(name: str, arguments: JsonObject, answer: str) -> Called:
    """A tool call and the text the model read back."""
    return Called(call_id=f"call-{name}", name=name, arguments=arguments, answer=answer)


def arrived(name: str, data: JsonObject, *, seq: int) -> Arrived:
    """A fact of the app's backend at this seq."""
    return Arrived(seq=seq, name=name, data=data, source="app")


def case_of_turns(
    *turns: Said,
    arrivals: tuple[Arrived, ...] = (),
    states: tuple[JsonObject, ...] = (),
    evidence: tuple[str, ...] = (),
) -> Case:
    """A case written by hand."""
    return Case(
        call=CALL, agent=AGENT, turns=turns, arrived=arrivals, states=states, evidence=evidence
    )


def a_judge(*verdicts: tuple[str, str]) -> AcmeLLM:
    """A judge model answering each question with the next verdict and its reason."""
    replies: list[list[str | dict[str, object]]] = [
        [{"name": "submit_verdict", "arguments": {"verdict": verdict, "reasoning": reason}}]
        for verdict, reason in verdicts
    ]
    return AcmeLLM(api_key="a judge's key", replies=replies)


def prompts_of(model: AcmeLLM) -> list[str]:
    """Every question the model was asked, as the text of its messages."""
    return [
        "\n".join(item.text_content or "" for item in request.items if item.type == "message")
        for request in model.requests
    ]
