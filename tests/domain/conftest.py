"""What the domain tests share: declarations and shapes to build from."""

from dataclasses import fields
from datetime import date

from pinecall.domain.agent import (
    AgentConfig,
    Docs,
    KnowledgeFile,
    MemoryPolicy,
    Model,
    PromptBlock,
    ToolSpec,
    Turn,
    Voice,
)
from pinecall.domain.call import CallContext, Contact, Route
from pinecall.domain.names import Channel, Json
from pinecall.domain.person import Member, Role
from pinecall.wire import parts as wire
from pinecall.wire.frames import WireModel

# ── the declaration ──

FIND_PATIENT = ToolSpec("find_patient", "Finds a patient by name and phone.", {"type": "object"})
A_DAY_AND_A_TIME: dict[str, Json] = {
    "type": "object",
    "properties": {"day": {"type": "string"}, "time": {"type": "string"}},
    "required": ["day", "time"],
}


def read_tool(
    name: str = "free_slots",
    parameters: dict[str, Json] | None = None,
    *,
    pii: frozenset[str] = frozenset(),
    preview: int | None = None,
    timeout_s: float = 30.0,
) -> ToolSpec:
    return ToolSpec(
        name,
        "Free slots of a day.",
        A_DAY_AND_A_TIME if parameters is None else parameters,
        pii=pii,
        preview=preview,
        timeout_s=timeout_s,
    )


# ── tools ──


# ── a call ──

MAIN_LINE = Route("clinics", "clinica-norte", "phone", "+34910000001")
WIDGET = Route("clinics", "clinica-norte", "web")
TODAY = date(2026, 9, 6)
ANA = Contact(phone="+34600000001", name="Ana")


def phone_call(
    *,
    channel: Channel = "phone",
    route: Route = MAIN_LINE,
    caller: str = "+34600000001",
    contact: Contact | None = ANA,
    holder: str | None = None,
) -> CallContext:
    return CallContext(
        call="CA_8f4a",
        channel=channel,
        direction="inbound",
        caller=caller,
        route=route,
        today=TODAY,
        contact=contact,
        holder=holder,
    )


def one_call(call: str = "CA_8f4a", metadata: dict[str, Json] | None = None) -> CallContext:
    return CallContext(
        call=call,
        channel="phone",
        direction="inbound",
        caller="+34600000001",
        route=MAIN_LINE,
        today=TODAY,
        metadata={} if metadata is None else metadata,
    )


# ── routes and numbers ──


# ── an org and its limits ──


# ── people and keys ──


def a_member(
    email: str = "berna@clinica.uy",
    name: str = "Berna",
    member_id: str = "m_1",
    role: Role = "developer",
) -> Member:
    return Member(id=member_id, org="clinica", email=email, name=name, role=role)


# ── what is set on top ──


# ── the day ──


# ── the wire: the core types and the generated ones agree ──

TWINS: list[tuple[str, frozenset[str], type[WireModel]]] = [
    ("AgentConfig", frozenset(field.name for field in fields(AgentConfig)), wire.AgentConfig),
    ("ToolSpec", frozenset(field.name for field in fields(ToolSpec)), wire.ToolSpec),
    ("Route", frozenset(field.name for field in fields(Route)), wire.Route),
    ("Contact", frozenset(field.name for field in fields(Contact)), wire.Contact),
    ("Voice", frozenset(field.name for field in fields(Voice)), wire.VoiceConfig),
    ("Model", frozenset(field.name for field in fields(Model)), wire.ModelConfig),
    ("Turn", frozenset(field.name for field in fields(Turn)), wire.TurnConfig),
    ("PromptBlock", frozenset(field.name for field in fields(PromptBlock)), wire.PromptBlockSpec),
    ("KnowledgeFile", frozenset(field.name for field in fields(KnowledgeFile)), wire.KnowledgeFile),
    ("Docs", frozenset(field.name for field in fields(Docs)), wire.DocsConfig),
    ("MemoryPolicy", frozenset(field.name for field in fields(MemoryPolicy)), wire.MemoryConfig),
]

# Wire fields with no core twin. `voice.name` is resolved to a provider and id by the gateway.
# `docs` is deprecated and ignored, kept on the wire so older apps still register.
RESOLVED_AT_THE_EDGE: dict[type[WireModel], frozenset[str]] = {
    wire.VoiceConfig: frozenset({"name"}),
    wire.AgentConfig: frozenset({"docs"}),
}
