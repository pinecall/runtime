"""Tests for the words of the domain: worlds, channels, slugs, numbers, scopes."""

import re
from dataclasses import FrozenInstanceError
from typing import get_args

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import (
    CHANNELS,
    ENVS,
    Channel,
    Env,
    parse_channel,
    parse_e164,
    parse_env,
    parse_slug,
)
from pinecall.domain.person import EVERY_SCOPE, KeyScope
from pinecall.wire.frames import WireModel
from tests.domain.conftest import (
    MAIN_LINE,
    RESOLVED_AT_THE_EDGE,
    TODAY,
    TWINS,
    WIDGET,
    a_member,
    one_call,
    phone_call,
    read_tool,
)


def test_a_pii_field_names_a_parameter_the_tool_has() -> None:
    assert read_tool(pii=frozenset({"day"})).pii == {"day"}
    with pytest.raises(DeclarationRefused, match=r"unknown: \['phone'\]"):
        read_tool(pii=frozenset({"phone"}))


def test_a_tool_name_is_one_word_a_model_can_call() -> None:
    assert read_tool(name="findPatient").name == "findPatient"
    for name in ("", "find patient", "find.patient", "1st_slot"):
        with pytest.raises(DeclarationRefused, match="one word"):
            read_tool(name=name)


def test_parameters_are_a_json_schema_object() -> None:
    with pytest.raises(DeclarationRefused, match="type object"):
        read_tool(parameters={"type": "array"})
    assert read_tool(parameters={"type": "object"}).parameter_names == frozenset()
    assert read_tool().parameter_names == {"day", "time"}


def test_a_preview_shows_at_least_one_item_and_a_timeout_is_positive() -> None:
    assert read_tool(preview=2).preview == 2
    with pytest.raises(DeclarationRefused, match="at least one"):
        read_tool(preview=0)
    with pytest.raises(DeclarationRefused, match="above 0"):
        read_tool(timeout_s=0)


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


def test_a_member_refuses_what_is_not_one_in_a_sentence() -> None:
    with pytest.raises(DeclarationRefused, match="one @ and a domain"):
        a_member(email="berna")
    with pytest.raises(DeclarationRefused, match="one @ and a domain"):
        a_member(email="berna @clinica.uy")
    with pytest.raises(DeclarationRefused, match="has a name"):
        a_member(name="  ")
    with pytest.raises(DeclarationRefused, match="names their id"):
        a_member(member_id="")


def test_the_words_here_are_the_ones_the_wire_declares() -> None:
    assert set(get_args(Env.__value__)) == set(ENVS)
    assert set(get_args(Channel.__value__)) == set(CHANNELS)
    assert set(get_args(KeyScope.__value__)) == set(EVERY_SCOPE)


@pytest.mark.parametrize(("ours", "declared", "theirs"), TWINS, ids=[name for name, _, _ in TWINS])
def test_every_wire_field_has_a_field_here_of_the_same_name(
    ours: str, declared: frozenset[str], theirs: type[WireModel]
) -> None:
    missing = set(theirs.model_fields) - declared - RESOLVED_AT_THE_EDGE.get(theirs, frozenset())
    assert not missing, f"{ours} lacks {sorted(missing)}, which {theirs.__name__} carries"
