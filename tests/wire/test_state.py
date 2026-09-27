"""Tests for the state a log reduces to."""

from typing import get_args

from pinecall.wire import state
from pinecall.wire.state import AgentTurn, Turn, UserTurn
from pinecall_protocol import state as their_state
from tests.wire.parity import mismatches


def test_the_state_and_its_parts_are_the_generated_ones_field_for_field() -> None:
    assert mismatches(state, their_state) == []


def test_a_turn_is_told_apart_by_its_role() -> None:
    union, discriminator = get_args(Turn.__value__)
    assert get_args(union) == (UserTurn, AgentTurn)
    assert discriminator.discriminator == "role"
