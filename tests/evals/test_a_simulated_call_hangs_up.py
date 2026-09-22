"""A simulated caller hangs up, because leaving the room is not the same thing."""

# Until it did, the agent stayed seated after the persona was done: its job never ended, the
# shutdown callback that SEALS the log (worker/entry.py:95,191) only ran once livekit closed the
# room on its own empty timeout, minutes later — and `call.summary` is the only place a
# recording's path is ever written (api/calls/recording.py:17). So a run that had just finished
# was answered with "has no call.summary yet" while its audio sat on the box, whole, unreachable.

from __future__ import annotations

import json
from typing import Any

import pytest
from livekit import api

from pinecall._settings import Settings
from pinecall.evals import dispatching

pytestmark = pytest.mark.unit

THE_CALL = "call_1f75440906a547b6aec7e14a"
THE_AGENT = "clinica-norte"
THE_FLEET = "pinecall"

A_BOX = Settings(livekit_api_key="APIkey", livekit_api_secret="secret", livekit_url="ws://box")


class FakeLiveKit:
    """LiveKitAPI as the dispatch uses it: an agent asked in, a room let go, a client closed."""

    made: list[FakeLiveKit] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.dispatched: list[Any] = []
        self.deleted: list[str] = []
        self.closed = False
        # The two services the dispatch reaches through, both answered by this one object.
        self.room = self
        self.agent_dispatch = self
        FakeLiveKit.made.append(self)

    async def create_dispatch(self, request: Any) -> None:
        self.dispatched.append(request)

    async def delete_room(self, request: Any) -> None:
        self.deleted.append(str(request.room))

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def a_livekit_that_records_what_it_was_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every LiveKitAPI the dispatch builds is this one, and the test reads what it was told."""
    FakeLiveKit.made = []
    monkeypatch.setattr(dispatching.api, "LiveKitAPI", FakeLiveKit)


async def test_the_room_is_deleted_when_the_call_is_over() -> None:
    """Deleting the room is the hangup: it ends the job, and the job's shutdown seals the log."""
    async with dispatching.a_dispatch(THE_CALL, THE_AGENT, THE_FLEET, A_BOX):
        pass

    livekit = FakeLiveKit.made[0]
    assert livekit.deleted == [THE_CALL]
    assert livekit.closed


async def test_the_agent_is_dispatched_before_anything_is_torn_down() -> None:
    """The room is asked for first and let go last: the order is the call."""
    async with dispatching.a_dispatch(THE_CALL, THE_AGENT, THE_FLEET, A_BOX):
        livekit = FakeLiveKit.made[0]
        assert len(livekit.dispatched) == 1
        assert livekit.deleted == []


async def test_a_room_that_is_already_gone_is_not_an_error() -> None:
    """The agent may hang up first, which seals the call by the same door. Nothing left to do."""

    async def already_gone(_request: Any) -> None:
        raise api.TwirpError("not_found", "room does not exist", status=404)

    async with dispatching.a_dispatch(THE_CALL, THE_AGENT, THE_FLEET, A_BOX):
        FakeLiveKit.made[0].delete_room = already_gone  # pyright: ignore[reportAttributeAccessIssue]

    assert FakeLiveKit.made[0].closed


async def test_the_client_is_closed_even_when_the_hangup_fails() -> None:
    """A leaked HTTP client is a ResourceWarning this suite treats as an error."""

    async def refused(_request: Any) -> None:
        raise api.TwirpError("unavailable", "livekit is not answering", status=503)

    async with dispatching.a_dispatch(THE_CALL, THE_AGENT, THE_FLEET, A_BOX):
        FakeLiveKit.made[0].delete_room = refused  # pyright: ignore[reportAttributeAccessIssue]

    assert FakeLiveKit.made[0].closed


async def test_the_dispatch_names_the_corner_the_call_is_in() -> None:
    """One worker answers every org, so a simulated call says whose it is or dies with NoRoute."""
    async with dispatching.a_dispatch(
        THE_CALL, THE_AGENT, THE_FLEET, A_BOX, org="clinica", env="sandbox", holder="m_carla"
    ):
        pass

    said = json.loads(FakeLiveKit.made[0].dispatched[0].metadata)
    assert said == {"agent": THE_AGENT, "org": "clinica", "env": "sandbox", "holder": "m_carla"}


async def test_the_dispatch_names_the_synthetic_caller_being_played() -> None:
    """The gateway holds the call but the WORKER writes call.started: this is the only road."""
    async with dispatching.a_dispatch(
        THE_CALL, THE_AGENT, THE_FLEET, A_BOX, org="clinica", persona="homeowner"
    ):
        pass

    assert json.loads(FakeLiveKit.made[0].dispatched[0].metadata)["persona"] == "homeowner"


async def test_a_dispatch_for_nobody_in_particular_names_no_persona() -> None:
    """A room somebody made by hand, and every real call: the field is absent, never empty."""
    async with dispatching.a_dispatch(THE_CALL, THE_AGENT, THE_FLEET, A_BOX, org="clinica"):
        pass

    assert "persona" not in json.loads(FakeLiveKit.made[0].dispatched[0].metadata)


async def test_a_dispatch_in_nobodys_corner_leaves_the_holder_out() -> None:
    """Production is the org's own: the field is absent rather than an empty string."""
    async with dispatching.a_dispatch(
        THE_CALL, THE_AGENT, THE_FLEET, A_BOX, org="clinica", env="production"
    ):
        pass

    assert "holder" not in json.loads(FakeLiveKit.made[0].dispatched[0].metadata)


async def test_the_dispatch_carries_the_callers_own_rule_for_the_judge_at_hang_up() -> None:
    """The worker writes call.started and the `persona` judge reads the rule there: one road."""
    async with dispatching.a_dispatch(
        THE_CALL,
        THE_AGENT,
        THE_FLEET,
        A_BOX,
        org="clinica",
        persona="homeowner",
        accepts_when="a price for Friday",
        declines_when="a call back",
    ):
        pass

    said = json.loads(FakeLiveKit.made[0].dispatched[0].metadata)
    assert (said["accepts_when"], said["declines_when"]) == ("a price for Friday", "a call back")


async def test_a_caller_that_wrote_no_rule_puts_none_on_the_dispatch() -> None:
    async with dispatching.a_dispatch(
        THE_CALL, THE_AGENT, THE_FLEET, A_BOX, org="clinica", persona="homeowner"
    ):
        pass

    said = json.loads(FakeLiveKit.made[0].dispatched[0].metadata)
    assert "accepts_when" not in said and "declines_when" not in said
