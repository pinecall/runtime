"""Token scopes: a closed set, each a bundle of yes and no the gateway never reasons past."""

from dataclasses import FrozenInstanceError, replace
from typing import get_args

import pytest

from pinecall.types import GRANTS, DeclarationRefused, Scope, grant_for
from pinecall.types.token import (
    BOUND_TO_ONE_CALL,
    LONGEST_VISIT_TTL_S,
    MINTED_FOR_A_VISIT,
    ONE_VISIT_TTL_S,
    READ_TTL_S,
    READS_ITS_OWN_CALL,
    SCOPES,
)

pytestmark = pytest.mark.unit


def test_token_scopes_are_a_closed_set() -> None:
    assert {"talk", "chat", "observe", "supervise", "participate", "read"} == SCOPES
    assert set(GRANTS) == SCOPES == set(get_args(Scope.__value__))
    assert all(GRANTS[scope].scope == scope for scope in SCOPES)
    with pytest.raises(DeclarationRefused, match="not a token scope"):
        grant_for("admin")


def test_talk_opens_one_session_once_for_a_minute_with_audio() -> None:
    talk = grant_for("talk")
    assert talk.connects and talk.audio and talk.single_use
    assert talk.ttl_s == ONE_VISIT_TTL_S == 60
    assert not talk.sends_verbs
    assert ONE_VISIT_TTL_S < LONGEST_VISIT_TTL_S == 600


def test_talk_reads_the_one_call_it_opens_so_a_browser_needs_one_token() -> None:
    """The participate grant rides the talk token: one string to speak and to watch its own call."""
    talk = grant_for("talk")
    assert talk.reads_log and talk.own_call_only
    assert {"talk", "chat", "participate", "read"} == READS_ITS_OWN_CALL
    assert {"talk", "chat"} == MINTED_FOR_A_VISIT


def test_chat_is_talk_with_no_microphone_and_it_hears() -> None:
    """It hears because LiveKit delivers text streams to subscribers only: the reply rides them."""
    assert grant_for("chat") == replace(grant_for("talk"), scope="chat", audio=False, hears=True)


def test_observe_reads_the_log_and_does_nothing_else() -> None:
    observe = grant_for("observe")
    assert observe.reads_log
    assert not (observe.connects or observe.audio or observe.sends_verbs or observe.own_call_only)


def test_only_supervise_sends_the_verbs() -> None:
    assert [scope for scope, grant in GRANTS.items() if grant.sends_verbs] == ["supervise"]
    assert grant_for("supervise").reads_log


def test_a_supervisor_publishes_audio_and_is_seen_because_a_hidden_seat_is_not_heard() -> None:
    """livekit delivers no track from a hidden participant: a hidden supervisor is a mute one."""
    supervise = grant_for("supervise")
    assert supervise.audio and not supervise.hidden
    assert not grant_for("observe").audio and grant_for("observe").hidden


def test_a_room_token_may_carry_the_browsers_scopes_and_the_desks_supervise_one() -> None:
    """Each is minted for ONE call and refused at every other; observe is a seat, not a read."""
    assert READS_ITS_OWN_CALL | {"supervise"} == BOUND_TO_ONE_CALL
    assert "observe" not in BOUND_TO_ONE_CALL


def test_participate_reads_its_own_call_and_no_other() -> None:
    participate = grant_for("participate")
    assert participate.reads_log and participate.own_call_only
    assert not participate.connects and not participate.sends_verbs


def test_a_grant_is_data_nobody_edits_at_runtime() -> None:
    any_field: str = "sends_verbs"
    with pytest.raises(FrozenInstanceError):
        setattr(grant_for("observe"), any_field, True)


def test_read_follows_one_call_for_hours_and_opens_no_room_and_steers_nothing() -> None:
    read = grant_for("read")
    assert read.reads_log and read.own_call_only and not read.single_use
    assert not read.connects and not read.audio and not read.hears and not read.sends_verbs
    assert read.ttl_s == READ_TTL_S == 4 * 60 * 60
    assert "read" not in MINTED_FOR_A_VISIT
