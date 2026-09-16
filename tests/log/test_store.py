"""The Store contract, run against both stores: a seq is born in append, and only there."""

import asyncio

import pytest

from pinecall.log.entry import Entry, ephemeral_by_default
from pinecall.log.store import LogSealed, Store
from pinecall_protocol import encode

# The one suite, twice. memory is ring 0 and always runs; postgres runs when the dev stack is up
# and skips with the URL in the reason when it is not. Every assertion below is the contract, so a
# store that passes here is a store the log layer can be handed without reading its code.
BACKENDS = [
    pytest.param("memory", marks=pytest.mark.unit),
    pytest.param("postgres", marks=pytest.mark.postgres),
]


@pytest.fixture(params=BACKENDS)
def store(request: pytest.FixtureRequest) -> Store:
    """Typed as the Protocol, so the checker proves both classes honour it."""
    return request.getfixturevalue(f"{request.param}_store")


async def test_the_first_entry_is_seq_one_and_each_append_hands_back_the_next(
    store: Store, agent: str, call: str
) -> None:
    ringing = await store.append(call, agent, "call.ringing", {"from": "+34600000001"})
    started = await store.append(call, agent, "call.started", {})
    assert (ringing.seq, started.seq) == (1, 2)
    assert await store.latest_seq(call) == 2


async def test_seqs_stay_contiguous_under_concurrent_appends(
    store: Store, agent: str, call: str
) -> None:
    entries = await asyncio.gather(
        *(store.append(call, agent, "custom", {"n": n}) for n in range(50))
    )
    assert sorted(entry.seq for entry in entries) == list(range(1, 51))
    assert [entry.seq for entry in await store.since(call)] == list(range(1, 51))


async def test_a_sealed_log_refuses_every_later_append(store: Store, agent: str, call: str) -> None:
    await store.append(call, agent, "call.started", {})
    await store.seal(call)
    with pytest.raises(LogSealed, match="has ended"):
        await store.append(call, agent, "turn.user", {"text": "too late"})
    await store.seal(call)
    assert await store.latest_seq(call) == 1


async def test_since_never_returns_a_seq_at_or_below_the_cursor(
    store: Store, agent: str, call: str
) -> None:
    for n in range(5):
        await store.append(call, agent, "custom", {"n": n})
    for after in range(0, 7):
        assert all(entry.seq > after for entry in await store.since(call, after=after))
    assert [entry.seq for entry in await store.since(call, after=3)] == [4, 5]
    assert await store.since(call, after=5) == []


async def test_since_pages_in_seq_order_and_stops_at_the_limit(
    store: Store, agent: str, call: str
) -> None:
    for n in range(5):
        await store.append(call, agent, "custom", {"n": n})
    assert [entry.seq for entry in await store.since(call, after=1, limit=2)] == [2, 3]
    assert await store.since(f"{call}-nobody") == []


async def test_an_ephemeral_append_takes_a_seq_out_of_the_same_space(
    store: Store, agent: str, call: str
) -> None:
    """The seq space is one. What differs is whether the row survives, and that is per store."""
    interim = await store.append(
        call, agent, "user.transcript", {"text": "hol", "final": False}, ephemeral=True
    )
    final = await store.append(call, agent, "turn.user", {"text": "hola"})
    assert (interim.seq, final.seq) == (1, 2) and interim.ephemeral and not final.ephemeral
    assert await store.latest_seq(call) == 2
    assert ephemeral_by_default("user.transcript") and not ephemeral_by_default("turn.user")


async def test_the_agents_own_log_has_a_seq_of_its_own(store: Store, agent: str, call: str) -> None:
    registered = await store.append(None, agent, "agent.registered", {"routes": []})
    ringing = await store.append(call, agent, "call.ringing", {})
    configured = await store.append(None, agent, "agent.configured", {})
    assert (registered.seq, ringing.seq, configured.seq) == (1, 1, 2)
    assert registered.call is None and ringing.call == call
    own = await store.agent_since(agent)
    assert [entry.type for entry in own] == ["agent.registered", "agent.configured"]
    assert [entry.seq for entry in await store.agent_since(agent, after=1)] == [2]


async def test_list_calls_names_every_call_the_agent_handled_oldest_first(
    store: Store, agent: str, call: str
) -> None:
    second = f"{call}-b"
    for one in (call, second, call):
        await store.append(one, agent, "custom", {})
    await store.append(f"{call}-other", f"{agent}-other", "custom", {})
    assert await store.list_calls(agent) == [call, second]
    assert await store.list_calls(f"{agent}-nobody") == []


async def test_a_call_nobody_wrote_to_has_seq_zero(store: Store, call: str) -> None:
    assert await store.latest_seq(call) == 0


async def test_the_store_hands_back_the_wires_envelope(store: Store, agent: str, call: str) -> None:
    entry = await store.append(call, agent, "call.started", {"channel": "phone"})
    assert isinstance(entry, Entry)
    assert encode(entry) == {
        "seq": 1,
        "ts": 1.0,
        "call": call,
        "agent": agent,
        "type": "call.started",
        "ephemeral": False,
        "data": {"channel": "phone"},
    }


# ── whose log, and the read across every log ────────────────────────────────────


async def test_a_log_is_the_first_orgs_that_claims_it_and_never_moves(
    store: Store, agent: str, call: str
) -> None:
    assert await store.owner(call, agent) is None
    await store.owned(call, agent, "clinica")
    await store.owned(call, agent, "tienda")
    await store.append(call, agent, "call.started", {})
    assert await store.owner(call, agent) == "clinica"


async def test_the_agents_own_log_has_an_owner_of_its_own(
    store: Store, agent: str, call: str
) -> None:
    await store.owned(None, agent, "clinica")
    assert await store.owner(None, agent) == "clinica"
    assert await store.owner(call, agent) is None


async def test_across_pages_the_metered_types_of_every_log_by_position(
    store: Store, agent: str, call: str
) -> None:
    other = f"{call}-b"
    await store.owned(call, agent, "clinica")
    await store.owned(other, agent, "tienda")
    await store.append(call, agent, "call.started", {})
    first = await store.append(call, agent, "call.summary", {"duration_s": 1})
    await store.append(other, agent, "user.transcript", {"text": "hola"}, ephemeral=True)
    second = await store.append(other, agent, "call.summary", {"duration_s": 2})
    mine = {call, other}
    rows = [row for row in await store.across(("call.summary",)) if row.entry.call in mine]
    assert [(row.org, row.entry.seq) for row in rows] == [
        ("clinica", first.seq),
        ("tienda", second.seq),
    ]
    assert rows[0].position < rows[1].position
    resumed = await store.across(("call.summary",), after=rows[0].position)
    assert [row.entry.call for row in resumed if row.entry.call in mine] == [other]
    assert await store.across(("call.summary",), after=rows[1].position) == []


async def test_an_orgs_calls_are_listed_newest_first_across_its_agents_and_nobody_elses(
    store: Store, agent: str, call: str
) -> None:
    """What the org's Sessions screen lists before anybody picks an agent."""
    org, other = f"org-{call}", f"other-{call}"
    second_agent, second, theirs = f"{agent}-b", f"{call}-b", f"{call}-theirs"
    await store.append(call, agent, "call.ringing", {})
    await store.owned(call, agent, org)
    await store.append(second, second_agent, "call.ringing", {})
    await store.owned(second, second_agent, org)
    await store.append(theirs, agent, "call.ringing", {})
    await store.owned(theirs, agent, other)
    assert await store.calls_of(org, 10) == [second, call]
    assert await store.calls_of(org, 1) == [second]
    assert await store.calls_of(other, 10) == [theirs]
    assert await store.calls_of(f"nobody-{call}", 10) == []


async def test_a_call_is_listed_by_its_corner_and_the_first_claim_stands(
    store: Store, agent: str, call: str
) -> None:
    """Two developers' sandbox calls and the telephone's, one org: three lists, not one."""
    org = f"org-{call}"
    bernas, carlas, phones = f"{call}-berna", f"{call}-carla", f"{call}-phone"
    for one, env, holder in (
        (bernas, "sandbox", "m_berna"),
        (carlas, "sandbox", "m_carla"),
        (phones, "production", None),
    ):
        await store.append(one, agent, "call.ringing", {})
        await store.owned(one, agent, org, env, holder)
    # A later claim of another corner changes nothing: the row says where the call was opened.
    await store.owned(bernas, agent, org, "production", None)

    assert await store.calls_of(org, 10) == [phones, carlas, bernas]
    assert await store.calls_of(org, 10, "sandbox", "m_berna") == [bernas]
    assert await store.calls_of(org, 10, "sandbox", "m_carla", agent) == [carlas]
    assert await store.calls_of(org, 10, "production", "") == [phones]
    assert await store.calls_of(org, 10, "sandbox", "") == []
    assert await store.calls_of(org, 10, "sandbox", "m_berna", f"{agent}-other") == []
