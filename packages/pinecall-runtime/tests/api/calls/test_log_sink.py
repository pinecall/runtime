"""The sink's rule for a token: it reads the one call it was minted for, and a code token none."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from pinecall.api.calls.log_sink import NOT_YOURS, refuse_another_call
from pinecall.auth.scopes import Reader
from pinecall.types import PRODUCTION

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("asked", ["call_1", "call_2", None])
def test_a_code_token_is_refused_every_call_and_every_agents_log(asked: str | None) -> None:
    """Its call is "" — never a call's id — so every door that names one, or none, says 403."""
    reader = Reader(
        projection="public", call="", scope="read", code="4821", agent="clinica", env=PRODUCTION
    )
    with pytest.raises(HTTPException) as refused:
        refuse_another_call(reader, asked)
    assert (refused.value.status_code, refused.value.detail) == (403, NOT_YOURS)


def test_a_log_token_still_reads_its_own_call() -> None:
    refuse_another_call(Reader(projection="public", call="call_1", scope="read"), "call_1")
