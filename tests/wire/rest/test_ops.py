"""Tests for the bodies of the box's own doors."""

from pinecall.wire.rest.ops import BoxMailResponse, PutDiallingRequest


def test_the_boxs_mailbox_says_from_by_the_wires_word() -> None:
    row = BoxMailResponse.model_validate(
        {
            "configured": False,
            "source": None,
            "host": None,
            "port": None,
            "security": None,
            "username": None,
            "from": None,
            "verified_at": None,
            "last_error": None,
        }
    )
    assert "from" in row.written()


def test_a_guard_left_out_is_left_out() -> None:
    assert PutDiallingRequest(per_minute=2).model_dump(exclude_none=True) == {"per_minute": 2}
