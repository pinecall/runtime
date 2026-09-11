"""GET /v1/calls/{id}/state: the fold, the memo, and the projection the caller's scope chooses."""

import json
import time
from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.api.agents.registry import Registry
from pinecall.auth.scopes import a_room_token
from pinecall.log import snapshots as memo
from pinecall.log.reduce import reduce
from pinecall.log.snapshots import Snapshots
from pinecall.log.store import MemoryStore
from pinecall.types import PRODUCTION
from pinecall_protocol import decode_entries, encode
from pinecall_protocol.defs import AgentConfig, StateFieldSpec
from pinecall_protocol.fixtures import GOLDEN_LOG
from tests.api.conftest import A_KEY, A_LIVEKIT

pytestmark = pytest.mark.unit

THE_CALL = "CA_8f4a2c"
THE_AGENT = "clinica-norte"
AN_OWNER = "app_the_state_endpoint"

Json = dict[str, Any]


async def load_the_golden(store: MemoryStore) -> int:
    """The golden log, entry by entry, through the door every writer uses. Returns its last seq."""
    entries = decode_entries(GOLDEN_LOG.read_text())
    last = 0
    for entry in entries:
        stored = await store.append(
            call=THE_CALL,
            agent=THE_AGENT,
            type=entry.type,
            data=entry.data,
            ephemeral=entry.ephemeral,
        )
        last = stored.seq
    return last


async def declare_the_golden_agent(registry: Registry) -> None:
    """Register and configure the agent, so the sink can read what it said about its state."""
    await registry.register(
        owner=AN_OWNER, org="clinica", env=PRODUCTION, slug=THE_AGENT, routes=[]
    )
    await registry.configure(
        AN_OWNER,
        PRODUCTION,
        THE_AGENT,
        AgentConfig(
            state_fields=[
                StateFieldSpec(name="patient", visibility="pii"),
                StateFieldSpec(name="slots", visibility="public"),
                StateFieldSpec(name="booking", visibility="public"),
            ]
        ),
    )


# starlette's TestClient is an httpx client, and httpx 0.x ships no stubs for the members this
# suite uses; it goes through one deliberately untyped handle so nothing else has to say so.
def read(gateway: TestClient, call: str, bearer: str | None) -> tuple[int, Json]:
    """One GET at this sink, as a status and, when there is one, a body."""
    headers = {} if bearer is None else {"Authorization": f"Bearer {bearer}"}
    client: Any = gateway
    answer: Any = client.get(f"/v1/calls/{call}/state", headers=headers)
    status: int = answer.status_code
    body: Json = answer.json() if status == 200 else {}
    return status, body


def as_the_key(gateway: TestClient, call: str = THE_CALL) -> Json:
    """The tenant's own reader: an API key at the door."""
    status, body = read(gateway, call, A_KEY)
    assert status == 200, f"the key was refused with {status}"
    return body


def as_a_participant(gateway: TestClient, token: str, call: str) -> Json:
    """A widget reading with a participate token, which travels as a bearer like a key does."""
    status, body = read(gateway, call, token)
    assert status == 200, f"the token was refused with {status}"
    return body


def a_token(call: str, for_seconds: float = 60) -> str:
    """A participate token minted by the test; minting for real is the tokens card's."""
    return a_room_token(call, "participate", time.time() + for_seconds, A_LIVEKIT)


# ── criterion 1: the fold, and the memo ─────────────────────────────────────────


async def test_the_state_is_the_whole_log_folded(
    gateway: TestClient, store: MemoryStore, registry: Registry
) -> None:
    """What the endpoint answers is exactly reduce() over every entry the store kept."""
    last_seq = await load_the_golden(store)
    await declare_the_golden_agent(registry)
    answer = as_the_key(gateway)
    whole = encode(reduce(await store.since(THE_CALL, 0, 1000)))
    assert answer["last_seq"] == last_seq == 125
    assert answer["live"] is False, "the golden call ended"
    assert answer["state"]["status"] == "ended"
    assert answer["state"]["app_state"]["booking"] == "BK-5521"
    assert len(answer["state"]["turns"]) == len(whole["turns"]) == 12
    assert {name: answer["state"][name] for name in whole if name != "app_state"} == {
        name: whole[name] for name in whole if name != "app_state"
    }


async def test_a_second_read_at_the_same_seq_does_not_fold_the_log_again(
    gateway: TestClient,
    store: MemoryStore,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hundred widgets cost one reduction; one more entry costs one more."""
    await load_the_golden(store)
    await declare_the_golden_agent(registry)
    folds = 0

    def counted(entries: Any) -> Any:
        nonlocal folds
        folds += 1
        return reduce(entries)

    monkeypatch.setattr(memo, "reduce", counted)
    first = as_the_key(gateway)
    second = as_the_key(gateway)
    assert folds == 1, "the second read at the same last_seq folded the log again"
    assert first == second
    await store.append(call=THE_CALL, agent=THE_AGENT, type="agent.state", data={"state": "idle"})
    third = as_the_key(gateway)
    assert folds == 2
    assert third["last_seq"] == 126


async def test_a_call_nobody_wrote_to_is_a_404(gateway: TestClient) -> None:
    assert read(gateway, "CA_nothing", A_KEY)[0] == 404


# ── criterion 2: the projection is chosen at the sink, by what the caller is ────


async def test_a_participate_token_for_another_call_never_reads_this_one(
    gateway: TestClient, store: MemoryStore, registry: Registry
) -> None:
    """A token is bound to one call id; the sink refuses every other one with 403."""
    await load_the_golden(store)
    await declare_the_golden_agent(registry)
    assert read(gateway, THE_CALL, a_token("CA_someone_else"))[0] == 403
    assert read(gateway, THE_CALL, a_token(THE_CALL))[0] == 200


async def test_an_expired_or_forged_token_reads_nothing(
    gateway: TestClient, store: MemoryStore
) -> None:
    """A token that does not verify is nobody, exactly like a key that does not: 401, no reason."""
    await load_the_golden(store)
    assert read(gateway, THE_CALL, a_token(THE_CALL, -1))[0] == 401
    assert read(gateway, THE_CALL, "pt_aaa.1.bbb")[0] == 401
    assert read(gateway, THE_CALL, None)[0] == 401
    assert read(gateway, THE_CALL, "pk_not_a_key")[0] == 401
    # A REAL token for another call is 403: it is a reader, and this is not its log.
    assert read(gateway, THE_CALL, a_token("call_somebody_elses", 60))[0] == 403


async def test_a_participant_reads_the_public_projection_and_the_key_reads_the_tenants(
    gateway: TestClient, store: MemoryStore, registry: Registry
) -> None:
    """One URL, two answers: the scope at the door decides, and nothing the caller sent does."""
    await load_the_golden(store)
    await declare_the_golden_agent(registry)
    seen = as_a_participant(gateway, a_token(THE_CALL), THE_CALL)["state"]
    assert set(seen) == {
        "seq",
        "status",
        "user_state",
        "agent_state",
        "live",
        "turns",
        "app_state",
        "room",
        "confirms",
        "transfer",
        "held",
        "events",
    }
    assert set(seen["app_state"]) == {"slots", "booking"}, "patient and stage are not the caller's"
    assert all("attributes" not in seat for seat in seen["room"]["participants"])
    assert "sip." not in json.dumps(seen)
    assert "Marta Ruiz" not in json.dumps(seen["app_state"])
    tenants = as_the_key(gateway)["state"]
    assert tenants["app_state"]["patient"] == "***", "the tenant sees the key, masked"
    assert tenants["app_state"]["stage"] == "booked", "undeclared is the tenant's, whole"


async def test_an_agent_nobody_holds_declared_nothing_so_the_public_state_is_empty(
    gateway: TestClient, store: MemoryStore
) -> None:
    """No live registration, no declaration: every field is the tenant's and public keeps none."""
    await load_the_golden(store)
    seen = as_a_participant(gateway, a_token(THE_CALL), THE_CALL)["state"]
    assert seen["app_state"] == {}
    assert as_the_key(gateway)["state"]["app_state"]["patient"] != "***"


# ── the memo is per call, not per process ──────────────────────────────────────


async def test_the_memo_answers_per_call(store: MemoryStore) -> None:
    """Two calls, two memos; the seq of one never satisfies the other."""
    kept = Snapshots(store)
    await store.append(call="CA_a", agent=THE_AGENT, type="agent.state", data={"state": "idle"})
    await store.append(call="CA_b", agent=THE_AGENT, type="agent.state", data={"state": "idle"})
    a = await kept.of("CA_a")
    b = await kept.of("CA_b")
    assert a is not None and b is not None
    assert a.state.call == "CA_a" and b.state.call == "CA_b"
    assert await kept.of("CA_nothing") is None
