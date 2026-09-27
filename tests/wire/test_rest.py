"""Tests for the bodies the doors answer and take."""

from pinecall.wire import rest
from pinecall.wire.rest import Heartbeat, Opening, Sealing, TokenMinted
from pinecall_protocol import rest as their_rest
from pinecall_protocol.rest import Minted
from tests.wire.parity import mismatches

# The doors of later steps answer the rest of their family.
NOT_YET = "is on the wire and not in"
# A public entry lacks the envelope's call and agent, so a page holds projected JSON, not entries.
PROJECTED = "LogPage.entries: type"


def test_every_body_both_declare_is_the_generated_one_field_for_field() -> None:
    assert [
        one for one in mismatches(rest, their_rest) if NOT_YET not in one and one != PROJECTED
    ] == []


def test_a_minted_token_is_the_protocols_minted_under_the_name_the_door_reads() -> None:
    assert set(TokenMinted.model_fields) == set(Minted.model_fields)


def test_the_worker_and_the_gateway_speak_bodies_the_tenants_never_see() -> None:
    their_names = set(vars(their_rest))
    assert not {Opening.__name__, Sealing.__name__, Heartbeat.__name__} & their_names
