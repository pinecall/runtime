"""The memory ledger: the three answers a spend can come back with, in the order they happen."""

import time

import pytest

from pinecall.tokens.ledger import TokenRecord
from pinecall.tokens.ledger_memory import MemoryTokens

pytestmark = pytest.mark.unit


def a_record(call: str) -> TokenRecord:
    return TokenRecord(
        call=call,
        org="clinica",
        agent="clinica-norte",
        scope="talk",
        expires_at=time.time() + 60,
    )


async def test_a_token_is_spent_once_and_the_ledger_remembers_it_was() -> None:
    ledger = MemoryTokens()
    await ledger.minted(a_record("call_1"))
    assert await ledger.spend("call_1") == "spent"
    assert await ledger.spend("call_1") == "already_spent"


async def test_a_call_nobody_minted_a_token_for_is_told_apart_from_a_spent_one() -> None:
    ledger = MemoryTokens()
    assert await ledger.spend("call_nobody") == "never_minted"
