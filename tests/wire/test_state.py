"""Tests for the state a log reduces to."""

from typing import get_args

from pinecall.wire.state import AgentTurn, Turn, UserTurn


def test_a_turn_is_told_apart_by_its_role() -> None:
    union, discriminator = get_args(Turn.__value__)
    assert get_args(union) == (UserTurn, AgentTurn)
    assert discriminator.discriminator == "role"
