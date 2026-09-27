"""Tests for the domain types: declarations, calls, routes, orgs, people, tuning and the day."""

import re
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, date, datetime
from typing import get_args

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.types import (
    A_CALL,
    BLANK,
    CHANNELS,
    DEFAULT_LAYOUT,
    ENVS,
    EVERY_SCOPE,
    KEY_SCOPES,
    QUOTAS,
    ROLE_SCOPES,
    ROLES,
    THE_ORGS_OWN,
    AgentConfig,
    CallContext,
    Channel,
    Contact,
    Corner,
    Direction,
    Docs,
    DocsMode,
    Env,
    EventSource,
    Greeting,
    Json,
    Kept,
    Key,
    KeyScope,
    KnowledgeFile,
    Lexicon,
    Member,
    MemoryPolicy,
    Model,
    PromptBlock,
    PromptRegion,
    QuotaName,
    Quotas,
    Role,
    Route,
    ToolSpec,
    Tuning,
    Turn,
    Versions,
    Visibility,
    Voice,
    key_scopes,
    new_call_id,
    parse_channel,
    parse_e164,
    parse_env,
    parse_role,
    parse_slug,
    parse_zone,
    today_in,
    whose,
)
from pinecall.wire import events as wire_events
from pinecall.wire import parts as wire
from pinecall.wire.frames import WireModel
from pinecall_protocol import defs

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


def test_an_agent_is_named_by_its_slug() -> None:
    assert AgentConfig("clinica-norte").slug == "clinica-norte"
    for slug in ("", "Clínica Norte", "clinica_norte", "-norte", "norte-"):
        with pytest.raises(DeclarationRefused, match="slug"):
            AgentConfig(slug)


def test_the_prompt_is_the_default_layout_until_the_app_declares_one() -> None:
    assert AgentConfig("clinica-norte").prompt == DEFAULT_LAYOUT
    assert [block.name for block in DEFAULT_LAYOUT] == ["identity", "knowledge", "tools", "view"]
    faq = (PromptBlock("identity", "static"), PromptBlock("faq", "static"))
    assert AgentConfig("clinica-norte", prompt=faq).prompt == faq
    with pytest.raises(DeclarationRefused, match=r"prompt block names repeat: \['faq'\]"):
        AgentConfig(
            "clinica-norte", prompt=(PromptBlock("faq", "static"), PromptBlock("faq", "dynamic"))
        )


def test_tool_names_are_unique_within_an_agent() -> None:
    with pytest.raises(DeclarationRefused, match=r"repeat: \['find_patient'\]"):
        AgentConfig("clinica-norte", tools=(FIND_PATIENT, FIND_PATIENT))
    agent = AgentConfig("clinica-norte", tools=(FIND_PATIENT,))
    assert agent.tools_by_name == {"find_patient": FIND_PATIENT}


def test_a_state_field_is_the_tenants_unless_declared() -> None:
    agent = AgentConfig("clinica-norte", state_fields={"slots": "public", "patient": "pii"})
    assert agent.visibility_of("slots") == "public"
    assert agent.visibility_of("patient") == "pii"
    assert agent.visibility_of("booking") == "tenant"


def test_an_outside_event_reaches_the_agent_only_as_declared() -> None:
    agent = AgentConfig("clinica-norte", events={"slot.released": frozenset({"app"})})
    assert agent.accepts("slot.released", "app")
    assert not agent.accepts("slot.released", "participant")
    assert not agent.accepts("anything.else", "app")
    with pytest.raises(DeclarationRefused, match="names who may send it"):
        AgentConfig("clinica-norte", events={"slot.released": frozenset()})
    with pytest.raises(DeclarationRefused, match="names who may send it"):
        AgentConfig("clinica-norte", events={"": frozenset({"app"})})


def test_a_pronunciation_is_a_word_and_how_it_is_said() -> None:
    assert AgentConfig("clinica-norte", says={"GSA": "ge ese a"}).says == {"GSA": "ge ese a"}
    with pytest.raises(DeclarationRefused, match=r"agent clinica-norte: a pronunciation"):
        AgentConfig("clinica-norte", says={"GSA": ""})


def test_docs_are_retrieved_per_turn_or_behind_a_search_tool() -> None:
    assert (Docs("clinica").mode, Docs("clinica").k) == ("retrieved", 8)
    assert Docs("clinica", mode="tool").mode == "tool"
    with pytest.raises(DeclarationRefused, match="name the knowledge base"):
        Docs("")
    with pytest.raises(DeclarationRefused, match="at least one chunk"):
        Docs("clinica", k=0)
    with pytest.raises(DeclarationRefused, match="never negative"):
        Docs("clinica", min_score=-1)


def test_a_knowledge_file_is_named_by_its_path() -> None:
    assert KnowledgeFile("faq.md", "We open at nine.").path == "faq.md"
    with pytest.raises(DeclarationRefused, match="named by its path"):
        KnowledgeFile("", "We open at nine.")


def test_a_memory_policy_says_what_to_keep_and_what_never() -> None:
    policy = MemoryPolicy(remember=("alergias", "su médico habitual"), forget=("pagos",))
    assert "pagos" in policy.forget
    assert "alergias" in policy.remember
    with pytest.raises(DeclarationRefused, match="both remember and forget"):
        MemoryPolicy(remember=("pagos",), forget=("pagos",))


def test_a_whole_declaration_holds_together() -> None:
    agent = AgentConfig(
        "clinica-norte",
        name="Clínica Norte",
        language="es-ES",
        voice=Voice("cartesia", voice_id="v-1"),
        llm=Model("anthropic", "claude-sonnet-5", temperature=0.2),
        knowledge="# Clínica Norte\nHorario de 9 a 20.",
        bases=(Docs("clinica"),),
        memory=MemoryPolicy(remember=("alergias",)),
        tools=(FIND_PATIENT,),
    )
    assert agent.knowledge is not None
    assert agent.knowledge.startswith("# Clínica Norte")
    assert [docs.base for docs in agent.bases] == ["clinica"]
    assert agent.memory is not None
    assert agent.memory.remember == ("alergias",)
    assert agent.voice is not None
    assert agent.voice.provider == "cartesia"


def test_a_greeting_is_say_or_reply_and_never_both_or_neither() -> None:
    assert Greeting(say="Buenas.").say == "Buenas."
    assert Greeting(reply="saluda y preséntate").reply is not None
    with pytest.raises(DeclarationRefused, match="both were declared"):
        Greeting(say="Buenas.", reply="saluda y preséntate")
    with pytest.raises(DeclarationRefused, match="neither was"):
        Greeting()


# Deepgram rejects eager_eot_threshold > eot_threshold, which would leave the call without STT.
def test_a_turn_that_would_guess_later_than_it_decides_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="cannot sit above"):
        Turn(eot_threshold=0.7, eager_eot_threshold=0.9)


def test_the_eager_bar_may_sit_on_the_other_one() -> None:
    assert Turn(eot_threshold=0.85, eager_eot_threshold=0.85).eager_eot_threshold == 0.85
    assert Turn(eager_eot_threshold=0.4).eot_threshold is None


@pytest.mark.parametrize("seconds", [0, 60, 600, 3600])
def test_a_voice_calls_limit_is_none_or_a_minute_to_an_hour(seconds: int) -> None:
    assert AgentConfig("clinica-norte", max_duration_s=seconds).max_duration_s == seconds
    assert Tuning(max_duration_s=seconds).max_duration_s == seconds


@pytest.mark.parametrize("seconds", [30, 59, 3601, -1])
def test_a_limit_outside_it_is_refused_in_words_that_say_the_range(seconds: int) -> None:
    with pytest.raises(DeclarationRefused, match="0 for no limit, or 60 to 3600 seconds"):
        AgentConfig("clinica-norte", max_duration_s=seconds)
    with pytest.raises(DeclarationRefused, match="0 for no limit, or 60 to 3600 seconds"):
        Tuning(max_duration_s=seconds)


# ── tools ──


def test_an_irreversible_tool_needs_a_confirm_template() -> None:
    with pytest.raises(DeclarationRefused, match="confirm template"):
        ToolSpec("book_slot", "Books a slot.", A_DAY_AND_A_TIME, side_effect="irreversible")
    booking = ToolSpec(
        "book_slot",
        "Books a slot.",
        A_DAY_AND_A_TIME,
        side_effect="irreversible",
        confirm="Le reservo el {day} a las {time}. ¿Lo confirmo?",
    )
    assert booking.confirm is not None


def test_a_read_tool_needs_no_yes_but_a_template_gates_any_tool() -> None:
    assert read_tool().confirm is None
    changed = ToolSpec("move_slot", "Moves a slot.", A_DAY_AND_A_TIME, confirm="¿Lo cambio?")
    assert changed.confirm is not None


def test_a_pii_field_names_a_parameter_the_tool_has() -> None:
    assert read_tool(pii=frozenset({"day"})).pii == {"day"}
    with pytest.raises(DeclarationRefused, match=r"unknown: \['phone'\]"):
        read_tool(pii=frozenset({"phone"}))


def test_a_tool_name_is_one_word_a_model_can_call() -> None:
    assert read_tool(name="findPatient").name == "findPatient"
    for name in ("", "find patient", "find.patient", "1st_slot"):
        with pytest.raises(DeclarationRefused, match="one word"):
            read_tool(name=name)


def test_a_tool_without_a_description_is_one_no_model_can_choose() -> None:
    with pytest.raises(DeclarationRefused, match="description"):
        ToolSpec("free_slots", "   ", A_DAY_AND_A_TIME)


def test_parameters_are_a_json_schema_object() -> None:
    with pytest.raises(DeclarationRefused, match="type object"):
        read_tool(parameters={"type": "array"})
    assert read_tool(parameters={"type": "object"}).parameter_names == frozenset()
    assert read_tool().parameter_names == {"day", "time"}


def test_a_preview_shows_at_least_one_item_and_a_timeout_is_positive() -> None:
    assert read_tool(preview=2).preview == 2
    with pytest.raises(DeclarationRefused, match="at least one"):
        read_tool(preview=0)
    with pytest.raises(DeclarationRefused, match="positive"):
        read_tool(timeout_s=0)


def test_when_is_the_apps_business_and_has_no_field_here() -> None:
    assert "when" not in {declared.name for declared in fields(ToolSpec)}


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


def test_a_call_comes_through_a_door_of_its_own_channel() -> None:
    assert phone_call().route is MAIN_LINE
    with pytest.raises(DeclarationRefused, match="web call cannot come through a phone route"):
        phone_call(channel="web", route=MAIN_LINE)


def test_a_call_knows_the_day_it_happens_on() -> None:
    assert phone_call().today == TODAY


def test_a_web_visitor_may_be_nobody_yet() -> None:
    visit = phone_call(channel="web", route=WIDGET, caller="visitor_8f4a", contact=None)
    assert visit.contact is None


def test_the_corner_is_the_dispatchs_and_defaults_to_the_orgs_own() -> None:
    assert phone_call().holder is None
    assert phone_call(holder="m_carla").holder == "m_carla"
    assert phone_call().env == MAIN_LINE.env


def test_the_metadata_is_the_apps_and_defaults_to_nothing() -> None:
    assert one_call().metadata == {}
    assert one_call(metadata={"campaign": "otoño"}).metadata == {"campaign": "otoño"}


def test_a_call_names_itself_and_its_caller() -> None:
    with pytest.raises(DeclarationRefused, match="names its call"):
        one_call(call="")
    with pytest.raises(DeclarationRefused, match="calling side"):
        phone_call(caller="")


def test_the_context_is_frozen_because_what_changes_is_in_the_log() -> None:
    any_field = "caller"
    with pytest.raises(FrozenInstanceError):
        setattr(phone_call(), any_field, "+34600000002")


def test_a_phone_call_is_remembered_under_the_number_that_called() -> None:
    assert phone_call().remembered_as == "+34600000001"


def test_a_resolved_contact_id_outranks_the_number() -> None:
    assert phone_call(contact=Contact(id="P-2231", phone="+34600000001")).remembered_as == "P-2231"


def test_a_web_visitor_with_no_id_is_nobody_to_memory() -> None:
    visitor = phone_call(channel="web", route=WIDGET, caller="visitor_1", contact=None)
    assert visitor.remembered_as is None
    named = phone_call(channel="web", route=WIDGET, caller="visitor_1", contact=Contact(name="Ana"))
    assert named.remembered_as is None


def test_a_call_id_minted_here_is_the_prefix_and_32_hex_digits() -> None:
    minted = new_call_id()
    assert minted.startswith(A_CALL)
    assert re.fullmatch(r"[0-9a-f]{32}", minted.removeprefix(A_CALL))
    assert minted != new_call_id()


# ── routes and numbers ──


def test_a_phone_route_answers_at_a_number_in_e164() -> None:
    assert MAIN_LINE.door == ("phone", "+34910000001")
    for number in (None, "910000001", "+34 910 000 001", "+0", "+" + "1" * 16):
        with pytest.raises(DeclarationRefused, match=re.escape("E.164")):
            Route("clinics", "clinica-norte", "phone", number=number)


def test_a_whatsapp_route_needs_a_number_too() -> None:
    with pytest.raises(DeclarationRefused, match="whatsapp route answers at a number"):
        Route("clinics", "clinica-norte", "whatsapp")


def test_the_web_widget_answers_at_no_number() -> None:
    assert WIDGET.door == ("web", None)
    with pytest.raises(DeclarationRefused, match="no number"):
        Route("clinics", "clinica-norte", "web", number="+34910000001")


def test_a_route_names_its_org_and_its_agent() -> None:
    for org, agent in (("", "clinica-norte"), ("clinics", "")):
        with pytest.raises(DeclarationRefused, match="org"):
            Route(org, agent, "web")


def test_the_same_door_is_the_same_door_whatever_the_org_or_the_label() -> None:
    by_day = Route("clinics", "clinica-norte", "phone", "+34910000001", label="main line")
    by_night = Route("night", "guardia", "phone", "+34910000001", label="after hours")
    assert by_day.door == by_night.door
    assert by_day != by_night


def test_a_number_is_trimmed_and_read_in_e164_or_refused() -> None:
    assert parse_e164(" +34910000000 ") == "+34910000000"
    with pytest.raises(DeclarationRefused, match=re.escape("E.164")):
        parse_e164("600123456")


def test_a_channel_a_world_and_a_slug_are_read_off_a_word_or_refused_with_the_choices() -> None:
    assert parse_channel("phone") == "phone"
    with pytest.raises(DeclarationRefused, match=r"phone.*web.*whatsapp"):
        parse_channel("sms")
    assert parse_env("sandbox") == "sandbox"
    with pytest.raises(DeclarationRefused, match=r"production.*sandbox"):
        parse_env("staging")
    assert parse_slug("clinica-norte") == "clinica-norte"
    with pytest.raises(DeclarationRefused, match="lowercase words joined by dashes"):
        parse_slug("Clínica")


# ── an org and its limits ──


def test_an_org_nobody_limited_has_no_limit_on_anything() -> None:
    quotas = Quotas()
    for name in QUOTAS:
        assert quotas.reached(name, 10_000) is None
        assert quotas.exceeded(name, 10_000) is None
        assert not quotas.switched_off(name)


def test_a_cap_is_reached_at_it_and_past_it_and_not_under_it() -> None:
    quotas = Quotas(memory_facts=100)
    assert quotas.reached("memory_facts", 99) is None
    assert quotas.reached("memory_facts", 100) == 100
    assert quotas.reached("memory_facts", 101) == 100


def test_a_push_fits_up_to_the_cap_and_not_one_chunk_past_it() -> None:
    quotas = Quotas(knowledge_chunks=1000)
    assert quotas.exceeded("knowledge_chunks", 999) is None
    assert quotas.exceeded("knowledge_chunks", 1000) is None
    assert quotas.exceeded("knowledge_chunks", 1001) == 1000


def test_zero_refuses_everything_and_is_how_a_plan_says_it_has_no_such_feature() -> None:
    free = Quotas(memory_facts=0, knowledge_chunks=0)
    assert free.reached("memory_facts", 0) == 0
    assert free.exceeded("knowledge_chunks", 1) == 0
    assert free.switched_off("memory_facts")
    assert free.switched_off("knowledge_chunks")


def test_a_cap_of_one_is_not_switched_off_and_no_cap_is_not_switched_off_either() -> None:
    assert not Quotas(memory_facts=1).switched_off("memory_facts")
    assert not Quotas().switched_off("knowledge_chunks")


def test_a_quota_is_a_count_and_a_negative_one_is_refused_by_name() -> None:
    with pytest.raises(DeclarationRefused, match="knowledge_chunks cannot be -1"):
        Quotas(knowledge_chunks=-1)
    with pytest.raises(DeclarationRefused, match="budget is euros"):
        Quotas(budget_eur=-1)


def test_the_quota_names_are_spelled_once_and_the_dataclass_has_a_field_for_each() -> None:
    assert set(QUOTAS) == set(Quotas().limits)
    assert set(QUOTAS) <= {declared.name for declared in fields(Quotas)}
    assert all(limit is None for limit in Quotas().limits.values())


# ── people and keys ──


def a_member(
    email: str = "berna@clinica.uy",
    name: str = "Berna",
    member_id: str = "m_1",
    role: Role = "developer",
) -> Member:
    return Member(id=member_id, org="clinica", email=email, name=name, role=role)


def test_every_role_presets_scopes_the_runtime_knows_and_admin_has_every_one() -> None:
    assert set(ROLE_SCOPES) == set(ROLES)
    assert all(scopes <= KEY_SCOPES for scopes in ROLE_SCOPES.values())
    assert ROLE_SCOPES["admin"] == KEY_SCOPES


def test_the_roles_widen_the_way_the_floor_does() -> None:
    assert ROLE_SCOPES["qa"] < ROLE_SCOPES["supervisor"] < ROLE_SCOPES["manager"]
    assert "app" in ROLE_SCOPES["developer"]
    assert "team" not in ROLE_SCOPES["developer"]
    assert "app" not in ROLE_SCOPES["manager"]


def test_a_members_scopes_are_their_roles_preset() -> None:
    assert a_member(role="qa").scopes == ROLE_SCOPES["qa"]
    assert a_member().status == "invited"
    assert a_member().agents == frozenset()


def test_an_admin_opens_production_and_anybody_else_only_when_granted() -> None:
    assert a_member(role="admin").opens_production
    assert not a_member().opens_production
    assert Member("m_2", "clinica", "ana@clinica.uy", "Ana", "qa", production=True).opens_production


def test_a_member_refuses_what_is_not_one_in_a_sentence() -> None:
    with pytest.raises(DeclarationRefused, match="one @ and a domain"):
        a_member(email="berna")
    with pytest.raises(DeclarationRefused, match="one @ and a domain"):
        a_member(email="berna @clinica.uy")
    with pytest.raises(DeclarationRefused, match="has a name"):
        a_member(name="  ")
    with pytest.raises(DeclarationRefused, match="names their id"):
        a_member(member_id="")


def test_a_role_is_read_off_a_word_and_a_word_that_is_none_is_refused_with_the_five() -> None:
    assert parse_role("supervisor") == "supervisor"
    with pytest.raises(DeclarationRefused, match=r"admin.*developer.*manager.*qa.*supervisor"):
        parse_role("owner")


def test_the_fleet_scope_is_never_a_default_and_the_words_are_read_or_refused() -> None:
    assert "fleet" in EVERY_SCOPE
    assert "fleet" not in KEY_SCOPES
    assert key_scopes(["calls", "app", "calls"]) == frozenset({"app", "calls"})
    with pytest.raises(DeclarationRefused, match="'root' is not a key scope"):
        key_scopes(["calls", "root"])


def test_a_key_opens_production_with_every_scope_but_fleet_unless_said() -> None:
    key = Key("k_1", "clinica")
    assert (key.env, key.scopes, key.expires_at) == ("production", KEY_SCOPES, None)
    assert Key("k_2", "clinica", env="sandbox", scopes=frozenset({"app"})).scopes == {"app"}


def test_the_orgs_own_rows_have_an_empty_holder_because_null_never_matches_a_key() -> None:
    assert whose(None) == THE_ORGS_OWN == ""
    assert whose("m_carla") == "m_carla"


# ── what is set on top ──


def test_nothing_set_is_every_knob_none_the_bases_with_the_rest() -> None:
    nothing = Tuning()
    assert (nothing.voice, nothing.llm, nothing.greeting, nothing.memory) == (None,) * 4
    assert nothing.knowledge is None
    assert nothing.bases is None
    assert Tuning(bases=()).bases == ()


@pytest.mark.parametrize("knob", ["voice", "tts", "tts_model", "stt", "llm", "knowledge"])
def test_a_blank_named_knob_is_refused_in_the_sentence_that_says_why(knob: str) -> None:
    blank: dict[str, str] = {knob: "   "}
    with pytest.raises(DeclarationRefused, match=re.escape(BLANK.format(field=knob))):
        Tuning(
            voice=blank.get("voice"),
            tts=blank.get("tts"),
            tts_model=blank.get("tts_model"),
            stt=blank.get("stt"),
            llm=blank.get("llm"),
            knowledge=blank.get("knowledge"),
        )


def test_an_opening_is_one_verb_here_as_it_is_on_the_class() -> None:
    with pytest.raises(DeclarationRefused, match="pick one"):
        Tuning(greeting=Greeting(say="Buenas.", reply="saluda y preséntate"))


def test_a_lexicon_refuses_a_blank_word_and_a_blank_spoken_form() -> None:
    assert Lexicon(said={"GSA": "ge ese a"}, heard=("Clínica Norte",)).heard == ("Clínica Norte",)
    with pytest.raises(DeclarationRefused, match="the lexicon"):
        Lexicon(said={"GSA": ""})
    with pytest.raises(DeclarationRefused, match="the lexicon"):
        Lexicon(heard=("Clínica Norte", " "))


def test_versions_none_is_a_corner_that_had_set_nothing() -> None:
    assert Versions() == Versions(config=None, lexicon=None)


def test_a_kept_value_carries_who_set_it_and_when() -> None:
    kept = Kept("m_1", 3, "berna", None, datetime.now(UTC), Tuning(voice="clara"))
    assert (kept.version, kept.value.voice) == (3, "clara")


# ── the day ──


def test_a_zone_is_an_iana_name_and_today_is_read_in_it() -> None:
    assert parse_zone("Europe/Madrid").key == "Europe/Madrid"
    assert isinstance(today_in("America/Montevideo"), date)
    with pytest.raises(DeclarationRefused, match="not an IANA time zone"):
        parse_zone("Mars/Olympus")


# ── the wire: the core types and the generated ones agree ──

TWINS: list[tuple[str, frozenset[str], type[WireModel]]] = [
    ("AgentConfig", frozenset(one.name for one in fields(AgentConfig)), wire.AgentConfig),
    ("ToolSpec", frozenset(one.name for one in fields(ToolSpec)), wire.ToolSpec),
    ("Route", frozenset(one.name for one in fields(Route)), wire.Route),
    ("Contact", frozenset(one.name for one in fields(Contact)), wire.Contact),
    ("Voice", frozenset(one.name for one in fields(Voice)), wire.VoiceConfig),
    ("Model", frozenset(one.name for one in fields(Model)), wire.ModelConfig),
    ("Turn", frozenset(one.name for one in fields(Turn)), wire.TurnConfig),
    ("PromptBlock", frozenset(one.name for one in fields(PromptBlock)), wire.PromptBlockSpec),
    ("KnowledgeFile", frozenset(one.name for one in fields(KnowledgeFile)), wire.KnowledgeFile),
    ("Docs", frozenset(one.name for one in fields(Docs)), wire.DocsConfig),
    ("MemoryPolicy", frozenset(one.name for one in fields(MemoryPolicy)), wire.MemoryConfig),
]

# Wire fields with no core twin. `voice.name` is resolved to a provider and id by the gateway.
# `docs` is deprecated and ignored, kept on the wire so older apps still register.
RESOLVED_AT_THE_EDGE: dict[type[WireModel], frozenset[str]] = {
    wire.VoiceConfig: frozenset({"name"}),
    wire.AgentConfig: frozenset({"docs"}),
}


def test_the_words_here_are_the_ones_the_sdks_were_generated_with() -> None:
    assert set(get_args(Env.__value__)) == set(get_args(defs.Env.__value__)) == set(ENVS)
    assert (
        set(get_args(Channel.__value__)) == set(get_args(defs.Channel.__value__)) == set(CHANNELS)
    )
    assert get_args(Direction.__value__) == get_args(defs.Direction.__value__)
    assert get_args(PromptRegion.__value__) == get_args(defs.PromptRegion.__value__)
    assert get_args(DocsMode.__value__) == get_args(defs.DocsMode.__value__)
    assert get_args(Visibility.__value__) == get_args(defs.Visibility.__value__)
    assert get_args(EventSource.__value__) == get_args(defs.EventSource.__value__)
    assert set(get_args(KeyScope.__value__)) == set(EVERY_SCOPE)


def test_the_quota_names_here_are_the_ones_a_refusal_may_say() -> None:
    assert wire_events.CreditsExhausted.model_fields["quota"].annotation is QuotaName
    assert set(QUOTAS) == set(get_args(QuotaName.__value__))


@pytest.mark.parametrize(("ours", "declared", "theirs"), TWINS, ids=[name for name, _, _ in TWINS])
def test_every_wire_field_has_a_field_here_of_the_same_name(
    ours: str, declared: frozenset[str], theirs: type[WireModel]
) -> None:
    missing = set(theirs.model_fields) - declared - RESOLVED_AT_THE_EDGE.get(theirs, frozenset())
    assert not missing, f"{ours} lacks {sorted(missing)}, which {theirs.__name__} carries"


def test_the_contact_is_the_same_shape_on_both_sides() -> None:
    assert {declared.name for declared in fields(Contact)} == set(wire.Contact.model_fields)


def test_a_corner_is_the_orgs_own_production_unless_said_otherwise() -> None:
    assert Corner("clinica") == Corner("clinica", "production", THE_ORGS_OWN)
    assert Corner("clinica", "sandbox", "m_berna").holder == "m_berna"
