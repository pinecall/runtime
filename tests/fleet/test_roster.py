"""Tests for the roster of each fleet."""

import asyncio
from collections.abc import Mapping

from pinecall.fleet.roster import (
    ENDED_TO_JUDGE,
    FORGOTTEN_AFTER_S,
    ROSTER_CHANNEL,
    SLOW_FIRST_AUDIO_S,
    STALE_AFTER_S,
    TURNS_TO_JUDGE,
    Roster,
    failing,
    worker_state,
)
from pinecall.process.signal import LocalSignal
from pinecall.wire.rest.fleet import HeartbeatRequest, WorkerStatus

SANDBOX = "pinecall-sandbox"


def beat(
    worker: str, *, active: int = 0, max_jobs: int | None = 4, load: float = 0.1
) -> HeartbeatRequest:
    """A heartbeat of the sandbox's fleet."""
    return HeartbeatRequest(
        fleet=SANDBOX, worker=worker, active=active, max_jobs=max_jobs, load=load, draining=False
    )


def test_a_fleet_nobody_heard_from_is_not_full() -> None:
    assert not Roster().totals(SANDBOX, 0.0).full


def test_the_free_seats_are_what_each_worker_holds_under_its_measure() -> None:
    roster = Roster()
    roster.report(beat("w1", active=1), 0.0)
    roster.report(beat("w2", active=3), 0.0)
    totals = roster.totals(SANDBOX, 1.0)
    assert (totals.workers, totals.active, totals.seats, totals.free) == (2, 4, 8, 4)


def test_a_worker_at_livekits_line_accepts_nothing_and_a_fleet_of_it_is_full() -> None:
    roster = Roster()
    roster.report(beat("w1", load=0.7), 0.0)
    assert roster.totals(SANDBOX, 0.0).full


def test_a_cpu_gated_worker_counts_no_seat_and_still_accepts() -> None:
    roster = Roster()
    roster.report(beat("w1", max_jobs=None), 0.0)
    totals = roster.totals(SANDBOX, 0.0)
    assert (totals.seats, totals.accepting) == (0, 1)


def test_one_fleet_full_leaves_the_other_open() -> None:
    roster = Roster()
    roster.report(beat("w1", load=0.9), 0.0)
    production = HeartbeatRequest(
        fleet="pinecall", worker="w9", active=0, max_jobs=4, load=0.1, draining=False
    )
    roster.report(production, 0.0)
    assert roster.totals(SANDBOX, 0.0).full
    assert not roster.totals("pinecall", 0.0).full


def test_a_silent_worker_is_no_capacity_and_is_forgotten_an_hour_later() -> None:
    roster = Roster()
    roster.report(beat("w1"), 0.0)
    assert roster.totals(SANDBOX, STALE_AFTER_S + 1).workers == 0
    assert [seat.worker for seat in roster.of(SANDBOX, STALE_AFTER_S + 1)] == ["w1"]
    assert roster.of(SANDBOX, FORGOTTEN_AFTER_S + 1) == []


def test_a_cordon_outlives_the_next_heartbeat_and_is_told_back() -> None:
    roster = Roster()
    roster.report(beat("w1"), 0.0)
    assert roster.cordon(SANDBOX, "w1")
    assert roster.report(beat("w1"), 1.0).cordoned
    assert roster.cordon(SANDBOX, "w1", on=False)
    assert not roster.report(beat("w1"), 2.0).cordoned


def test_cordoning_a_name_nobody_has_is_refused() -> None:
    assert not Roster().cordon(SANDBOX, "ghost")


FAILING: dict[str, float] = {
    "ended": ENDED_TO_JUDGE,
    "failed": ENDED_TO_JUDGE // 2 + 1,
    "errors": 3,
}


SLOW: dict[str, float] = {"turns": TURNS_TO_JUDGE, "first_audio_p95_s": SLOW_FIRST_AUDIO_S + 1}


def minute_of(worker: str, minute: Mapping[str, float]) -> HeartbeatRequest:
    """A heartbeat of the sandbox's fleet that carries its last minute."""
    return beat(worker).model_copy(update=minute)


def test_the_heartbeats_last_minute_is_kept_with_the_worker() -> None:
    roster = Roster()
    roster.report(minute_of("w1", SLOW), 0.0)
    (seat,) = roster.of(SANDBOX, 1.0)
    assert (seat.turns, seat.first_audio_p95_s) == (TURNS_TO_JUDGE, SLOW_FIRST_AUDIO_S + 1)


def test_a_worker_whose_calls_fail_is_not_counted_while_another_takes_calls() -> None:
    roster = Roster()
    roster.report(minute_of("w1", FAILING), 0.0)
    roster.report(beat("w2"), 0.0)
    assert roster.totals(SANDBOX, 1.0).accepting == 1
    seats = roster.of(SANDBOX, 1.0)
    assert [worker_state(seat, seats, 1.0) for seat in seats] == ["failing", "accepting"]


def test_a_worker_whose_callers_wait_is_not_counted_while_another_takes_calls() -> None:
    roster = Roster()
    roster.report(minute_of("w1", SLOW), 0.0)
    roster.report(beat("w2"), 0.0)
    assert roster.totals(SANDBOX, 1.0).accepting == 1


# The box runs one worker per world: the line may never leave a fleet with nobody to take a call.
def test_a_fleet_whose_every_worker_fails_still_counts_them_all() -> None:
    roster = Roster()
    roster.report(minute_of("w1", FAILING), 0.0)
    totals = roster.totals(SANDBOX, 1.0)
    assert (totals.accepting, totals.full) == (1, False)
    (seat,) = roster.of(SANDBOX, 1.0)
    assert worker_state(seat, [seat], 1.0) == "accepting"


def test_a_few_calls_or_a_few_turns_say_nothing_about_a_worker() -> None:
    few = {"ended": ENDED_TO_JUDGE - 1, "failed": ENDED_TO_JUDGE - 1}
    short = {"turns": TURNS_TO_JUDGE - 1, "first_audio_p95_s": SLOW_FIRST_AUDIO_S + 1}
    assert not failing(minute_as_seat(few))
    assert not failing(minute_as_seat(short))
    assert not failing(minute_as_seat({}))


def test_a_worker_of_an_older_release_carries_no_minute_and_is_never_failing() -> None:
    roster = Roster()
    roster.report(beat("w1"), 0.0)
    (seat,) = roster.of(SANDBOX, 1.0)
    assert seat.ended is None
    assert not failing(seat)


def minute_as_seat(minute: Mapping[str, float]) -> WorkerStatus:
    """A worker's status as the roster keeps it, with this minute."""
    roster = Roster()
    roster.report(minute_of("w1", minute), 0.0)
    (seat,) = roster.of(SANDBOX, 0.0)
    return seat


# Workers beat on whichever gateway the balancer hands them: every gateway counts them all.
async def test_heartbeats_split_across_two_gateways_add_up_on_both_and_so_does_a_cordon() -> None:
    signal = LocalSignal()
    here, there = Roster(signal), Roster(signal)
    here.shared.every_s = there.shared.every_s = 0.01
    shares = await signal.subscribe(ROSTER_CHANNEL)
    await here.start()
    await there.start()
    here.report(beat("w1", active=1), 0.0)
    there.report(beat("w2", active=3), 0.0)

    def both_count_both() -> bool:
        return all(roster.totals(SANDBOX, 1.0).workers == 2 for roster in (here, there))

    async with asyncio.timeout(2):
        while not both_count_both():
            await anext(shares)
            await asyncio.sleep(0)
    assert there.totals(SANDBOX, 1.0).active == 4
    assert here.cordon(SANDBOX, "w2")
    async with asyncio.timeout(2):
        while not there.report(beat("w2", active=3), 2.0).cordoned:
            await anext(shares)
            await asyncio.sleep(0)
    shares.close()
    await here.close()
    await there.close()
