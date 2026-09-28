"""Tests for the bodies of the account doors."""

from datetime import UTC, datetime

from pinecall.domain.person import ROLE_SCOPES, Key, Member
from pinecall.wire.rest.accounts import (
    FirstKeyResponse,
    KeyIssuedResponse,
    MemberRow,
    OrgMailRequest,
    OrgMailResponse,
    SignInRequest,
)

ANA = Member("m_ana", "org_1", "ana@clinica.test", "Ana", "manager", frozenset({"b", "a"}))


def test_a_member_row_sorts_the_agents_and_names_the_roles_scopes() -> None:
    row = MemberRow.of(ANA)
    assert row.agents == ["a", "b"]
    assert row.scopes == sorted(ROLE_SCOPES["manager"])
    assert (row.status, row.production, row.verified) == ("invited", False, False)


def test_a_key_issued_says_the_key_once_and_the_scopes_sorted() -> None:
    key = Key("k_1", "org_1", label="phone", scopes=frozenset({"talk", "app"}), subject="m_ana")
    written = KeyIssuedResponse.of(key, "pc_live_x").written()
    assert written == {
        "key": "pc_live_x",
        "key_id": "k_1",
        "org": "org_1",
        "label": "phone",
        "env": "production",
        "scopes": ["app", "talk"],
        "subject": "m_ana",
        "name": None,
    }


def test_a_first_key_is_the_key_issued_and_the_member_beside_it_flat() -> None:
    issued = KeyIssuedResponse.of(Key("k_1", "org_1"), "pc_live_x")
    first = FirstKeyResponse(**issued.model_dump(), member=MemberRow.of(ANA))
    assert set(first.written()) == {*issued.written(), "member"}
    assert (first.key, first.member.id) == ("pc_live_x", "m_ana")


def test_a_login_body_may_leave_everything_out_and_the_door_says_what_is_missing() -> None:
    body = SignInRequest.read({"code": "lc_x"}, "login")
    assert (body.code, body.email, body.password, body.org, body.device) == (
        "lc_x",
        None,
        None,
        None,
        None,
    )


def test_a_mailbox_names_who_it_is_from_by_the_wire_word_from() -> None:
    wanted = OrgMailRequest.read(
        {"host": "smtp.clinica.test", "port": 587, "from": "Clinica <no-reply@clinica.test>"},
        "mail",
    )
    assert (wanted.sender, wanted.security, wanted.username, wanted.password) == (
        "Clinica <no-reply@clinica.test>",
        "starttls",
        "",
        "",
    )
    shown = OrgMailResponse.model_validate(
        {
            "configured": True,
            "host": "smtp.clinica.test",
            "port": 587,
            "security": "starttls",
            "username": "",
            "from": wanted.sender,
            "verified_at": datetime(2026, 9, 28, tzinfo=UTC),
            "last_error": None,
        }
    )
    assert shown.written()["from"] == wanted.sender
