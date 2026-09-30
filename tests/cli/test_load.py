"""Tests for `load`: synthetic calls held against a real gateway, and what the run reports."""

import argparse
import json
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

from pinecall.cli._load import (
    GATEWAY_KINDS,
    Plan,
    Script,
    Tally,
    loaded,
    log_holds,
    percentile,
    report_of,
    rising,
    run_load,
    script_of,
    starts_of,
    written_by_worker,
)
from pinecall.cli.main import verbs
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.org import Quotas
from pinecall.fleet.client import GatewayClient, gateway_at
from pinecall.process.settings import Settings
from pinecall.session.call import MOST_A_BATCH
from pinecall.tenancy.admission import set_quotas
from pinecall.wire.frames import Entry
from tests.conftest import AGENT, Knocking, postgres

GOLDEN = Path(__file__).parents[1] / "wire/golden/call-log.json"

# The golden call lasts 53.5 s; a hundredth of it lasts about half a second.
SHRUNK = 100

CALLS_OF_THE_ORG = "select call from call_log_head where org = %(org)s and call is not null"


def golden() -> list[Entry]:
    """The whole real call the wire is pinned to."""
    return [Entry.model_validate(entry) for entry in json.loads(GOLDEN.read_text())]


def shrunk() -> Script:
    """The golden call's script, its gaps a hundredth as long."""
    first = golden()[0].ts
    return script_of(
        [entry.model_copy(update={"ts": first + (entry.ts - first) / SHRUNK}) for entry in golden()]
    )


def plan_of(knocking: Knocking, *, calls: int, minutes: float) -> Plan:
    """A run of the org's agent on the shrunk script, ramped over a third of a second."""
    return Plan(
        org=knocking.org.id,
        agent=AGENT,
        script=shrunk(),
        calls=calls,
        ramp_s=0.3,
        hold_s=minutes * 60,
    )


def knocks_of(knocking: Knocking) -> Callable[[], GatewayClient]:
    """How a slot of a run knocks: a client of its own on the sandbox fleet's key."""
    return partial(gateway_at, knocking.url, knocking.fleet["sandbox"])


def an_entry(seq: int, kind: str, *, ephemeral: bool = False) -> Entry:
    """An entry of a call's log, as the gateway answers an append."""
    return Entry(
        seq=seq, ts=0.0, call="call_1", agent=AGENT, type=kind, ephemeral=ephemeral, data={}
    )


async def durable_logs(knocking: Knocking) -> dict[str, list[Entry]]:
    """Every call of the org, and the durable entries of a worker's kinds its log holds."""
    pool = knocking.gateway.connections.pool
    async with pool.connection() as connection:
        rows = await (
            await connection.execute(CALLS_OF_THE_ORG, {"org": knocking.org.id})
        ).fetchall()
    store = knocking.gateway.logs.store
    return {
        str(row["call"]): [
            entry for entry in await store.whole(str(row["call"])) if written_by_worker(entry.type)
        ]
        for row in rows
    }


def test_the_kinds_the_gateway_writes_are_left_out_and_the_workers_replayed() -> None:
    kinds = {step.type for step in (*shrunk().steps, shrunk().ending)}
    assert not kinds & GATEWAY_KINDS
    assert {"call.started", "turn.user", "turn.agent", "metrics.llm", "call.ended"} <= kinds
    assert not written_by_worker("call.summary")
    assert written_by_worker("user.transcript")


def test_the_script_keeps_the_logs_order_and_never_steps_back_in_time() -> None:
    script = shrunk()
    times = [step.at_s for step in script.steps]
    worker = [entry for entry in golden() if written_by_worker(entry.type)]
    assert times == sorted(times)
    assert [step.type for step in script.steps] == [
        entry.type for entry in worker if entry.type != "call.ended"
    ]
    assert script.ending.type == "call.ended"
    assert script.ending.event.written() == next(e.data for e in worker if e.type == "call.ended")


def test_a_script_with_no_call_ended_is_given_one_at_its_last_moment() -> None:
    entries = [entry for entry in golden()[:20] if entry.type != "call.ended"]
    script = script_of(entries)
    assert script.ending.type == "call.ended"
    assert script.ending.at_s == pytest.approx(entries[-1].ts - entries[0].ts)
    assert script.ending.event.written()["ended_by"] == "caller"


def test_the_ramp_starts_the_calls_evenly_and_all_at_once_with_none() -> None:
    assert starts_of(4, 2.0) == [0.0, 0.5, 1.0, 1.5]
    assert starts_of(3, 0.0) == [0.0, 0.0, 0.0]


def test_a_percentile_is_the_nearest_rank_and_nothing_for_no_sample() -> None:
    samples = [float(n) for n in range(1, 101)]
    assert percentile(samples, 0.5) == 50.0
    assert percentile(samples, 0.99) == 99.0
    assert percentile([7.0], 0.99) == 7.0
    assert percentile([], 0.5) is None


def test_a_log_with_a_duplicate_or_a_hole_is_not_what_was_sent() -> None:
    sent = [an_entry(2, "turn.user"), an_entry(3, "user.transcript", ephemeral=True)]
    sent.append(an_entry(4, "turn.agent"))
    kept = [an_entry(1, "call.ringing"), an_entry(2, "turn.user"), an_entry(4, "turn.agent")]
    assert log_holds(sent, kept)
    assert not log_holds(sent, [*kept, an_entry(5, "turn.agent")])
    assert not log_holds(sent, kept[:2])
    assert rising(sent)
    assert not rising([an_entry(3, "turn.user"), an_entry(3, "turn.agent")])


def test_the_report_warns_when_the_generators_own_loop_lagged() -> None:
    calm = report_of(Tally(lag_ms=[1.0] * 100), 60.0)
    lagging = report_of(Tally(lag_ms=[80.0] * 100), 60.0)
    assert calm[-1] == "loop lag ms: p99 1.0"
    assert lagging[-1].startswith("warning: this generator's own loop lagged 80.0 ms")


def test_the_verb_is_declared_with_its_flags() -> None:
    args = verbs().parse_args(
        [
            *("load", "--org", "o", "--agent", "a", "--script", "s.json"),
            *("--calls", "3", "--ramp", "10", "--minutes", "2"),
        ]
    )
    assert (args.calls, args.ramp, args.minutes) == (3, 10.0, 2.0)
    assert args.run.__name__ == "loaded"


def test_a_run_needs_every_flag() -> None:
    with pytest.raises(SystemExit):
        verbs().parse_args(["load", "--org", "o", "--agent", "a", "--calls", "3"])


@postgres
async def test_a_small_run_leaves_sealed_logs_holding_exactly_what_it_sent(
    knocking: Knocking,
) -> None:
    plan = plan_of(knocking, calls=3, minutes=0.02)
    tally = await run_load(knocks_of(knocking), plan)
    logs = await durable_logs(knocking)
    whole = [step for step in plan.script.steps if not step.ephemeral]
    for call, kept in logs.items():
        assert await knocking.gateway.logs.store.sealed(call)
        replayed = [(entry.type, entry.data) for entry in kept]
        prefix = [(step.type, step.event.written()) for step in whole[: len(replayed) - 1]]
        ending = (plan.script.ending.type, plan.script.ending.event.written())
        assert replayed == [*prefix, ending]
    assert tally.opened == tally.sealed == len(logs) >= plan.calls
    assert any(len(kept) == len(whole) + 1 for kept in logs.values())
    assert tally.durable == sum(len(kept) for kept in logs.values())
    assert tally.most_held == plan.calls
    assert tally.held == 0
    assert tally.refusals == {}
    assert tally.wrong == 0
    assert tally.verified + sum(tally.unread.values()) == tally.sealed
    assert sum(tally.batches) == tally.sent == len(tally.append_ms)
    assert max(tally.batches) <= MOST_A_BATCH
    lines = report_of(tally, plan.hold_s)
    assert "refusals: none" in lines
    assert f"calls opened: {tally.opened}" in lines
    assert any(line.startswith("entries per batch: p50 ") for line in lines)


@postgres
async def test_each_call_at_once_knocks_on_a_client_of_its_own_closed_at_the_end(
    knocking: Knocking,
) -> None:
    made: list[GatewayClient] = []

    def knock() -> GatewayClient:
        made.append(gateway_at(knocking.url, knocking.fleet["sandbox"]))
        return made[-1]

    plan = plan_of(knocking, calls=3, minutes=0.01)
    tally = await run_load(knock, plan)
    assert len(made) == plan.calls
    assert tally.opened >= plan.calls
    assert all(client.http.is_closed for client in made)


@postgres
async def test_a_call_refused_is_counted_and_the_run_goes_on(knocking: Knocking) -> None:
    pool = knocking.gateway.connections.pool
    await set_quotas(pool, knocking.org.id, "sandbox", Quotas(concurrent_calls=1))
    tally = await run_load(knocks_of(knocking), plan_of(knocking, calls=2, minutes=0.01))
    assert tally.refusals["429"] >= 1
    assert tally.opened == tally.sealed >= 1
    assert len(await durable_logs(knocking)) == tally.opened


def test_the_verb_refuses_a_run_with_no_fleet_key(tmp_path: Path) -> None:
    args = argparse.Namespace(
        org="o", agent="a", script=str(tmp_path / "none.json"), calls=1, ramp=0.0, minutes=0.0
    )
    with pytest.raises(DeclarationRefused, match="PINECALL_WORKER_KEY"):
        loaded(Settings.model_validate({}), args)
