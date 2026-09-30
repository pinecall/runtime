"""Tests for what a call's log keeps of a private value: masked in the log, sealed beside it."""

import logging

import pytest
from cryptography.fernet import Fernet

from pinecall.domain.agent import AgentConfig, ToolSpec
from pinecall.domain.names import JsonObject
from pinecall.log.private import MASK, kept_aside, masked_state, opened_whole, restored, split
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.process.connections import vault_of
from tests.conftest import postgres
from tests.log.conftest import AGENT, entry

LOOKUP = ToolSpec(
    name="find_patient",
    description="Find the patient by their phone.",
    parameters={
        "type": "object",
        "properties": {"phone": {"type": "string"}, "clinic": {"type": "string"}},
    },
    pii=frozenset({"phone"}),
)

DECLARED = AgentConfig(
    AGENT, tools=(LOOKUP,), state_fields={"slots": "public", "patient": "pii", "notes": "tenant"}
)

CALLED: JsonObject = {
    "call_id": "c1",
    "name": "find_patient",
    "arguments": {"phone": "+34600111222", "clinic": "centro"},
}

STATE: JsonObject = {"slots": ["12:00"], "patient": {"name": "Ana"}, "notes": "first visit"}

KEY = Fernet.generate_key().decode()


def test_a_tools_pii_arguments_are_masked_and_kept_apart() -> None:
    written = split("tool.call", CALLED, DECLARED)
    assert written.kept["arguments"] == {"phone": MASK, "clinic": "centro"}
    assert written.private == {"arguments": {"phone": "+34600111222"}}


def test_the_states_pii_fields_are_masked_wherever_an_entry_carries_the_state() -> None:
    for kind in ("state.changed", "call.attached"):
        written = split(kind, {"state": STATE, "changed": ["patient"]}, DECLARED)
        assert written.kept == {"state": {**STATE, "patient": MASK}, "changed": ["patient"]}
        assert written.private == {"state": {"patient": {"name": "Ana"}}}


def test_nothing_is_kept_apart_where_nothing_was_declared_private() -> None:
    undeclared = split("tool.call", {**CALLED, "name": "book"}, DECLARED)
    words = split("turn.user", {"text": "my phone is +34600111222"}, DECLARED)
    nothing_of_it = split("tool.call", {**CALLED, "arguments": {"clinic": "centro"}}, DECLARED)
    for written, data in (
        (undeclared, {**CALLED, "name": "book"}),
        (words, {"text": "my phone is +34600111222"}),
        (nothing_of_it, {**CALLED, "arguments": {"clinic": "centro"}}),
    ):
        assert (written.kept, written.private) == (data, {})


def test_a_masked_entry_is_restored_to_what_its_writer_wrote() -> None:
    written = split("tool.call", CALLED, DECLARED)
    kept = entry("tool.call", written.kept)
    assert restored(kept, written.private).data == CALLED
    assert restored(kept, None) is kept


def test_masking_twice_changes_nothing() -> None:
    once = masked_state(STATE, DECLARED)
    assert masked_state(once, DECLARED) == once == {**STATE, "patient": MASK}


@postgres
async def test_the_log_keeps_the_mask_and_the_value_is_sealed_beside_it(
    store: Store, pool: Pool, call: str
) -> None:
    vault = vault_of(KEY)
    written = split("tool.call", CALLED, DECLARED)
    kept = await store.append(call, AGENT, "tool.call", written.kept, ephemeral=False)
    await kept_aside(store, vault, call, [(kept.seq, written.private)])
    await kept_aside(store, vault, call, [(kept.seq, written.private)])
    async with pool.connection() as connection:
        rows = await (await connection.execute("SELECT * FROM call_private")).fetchall()
        logged = await (
            await connection.execute("SELECT data::text AS data FROM call_log")
        ).fetchall()
    assert "+34600111222" not in str([row["data"] for row in logged])
    assert [(row["log"], row["seq"]) for row in rows] == [(call, kept.seq)]
    assert "+34600111222" not in str(rows[0]["sealed"])
    assert [item.data for item in await opened_whole(store, vault, call)] == [CALLED]
    assert [item.data for item in await store.whole(call)] == [written.kept]


@postgres
async def test_a_value_sealed_under_a_key_the_vault_no_longer_lists_stays_masked(
    store: Store, call: str, caplog: pytest.LogCaptureFixture
) -> None:
    written = split("state.changed", {"state": STATE, "changed": []}, DECLARED)
    kept = await store.append(call, AGENT, "state.changed", written.kept, ephemeral=False)
    await kept_aside(store, vault_of(KEY), call, [(kept.seq, written.private)])
    with caplog.at_level(logging.WARNING):
        opened = await opened_whole(store, vault_of(Fernet.generate_key().decode()), call)
    assert [item.data for item in opened] == [written.kept]
    assert "no longer lists" in caplog.text
