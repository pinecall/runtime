"""The dispatch an outbound call opens with: into the call's own room, to this instance's fleet."""

import pytest

from pinecall._settings import Settings
from pinecall.routes import dispatching as dispatching_module
from pinecall.routes.dispatching import Dialling, Job, dispatches_for
from tests.routes.sfu import TheSfu

pytestmark = pytest.mark.unit


async def test_a_placed_call_is_dispatched_to_the_fleet_the_instance_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two instances share one SFU: what the sandbox places, its own workers answer."""
    sfu = TheSfu()
    monkeypatch.setattr(dispatching_module.api, "LiveKitAPI", sfu)
    dispatches = dispatches_for(
        Settings(livekit_api_key="k", livekit_api_secret="s" * 32, fleet="pinecall-sandbox")
    )
    assert dispatches is not None
    dialling = Dialling(
        trunk="ST_out_0", to="+34600000000", shown="+34910000000", max_duration_s=60
    )
    await dispatches.started(
        Job(call="call_1", agent="recepcion", org="clinica", env="sandbox", dialling=dialling)
    )
    [asked] = sfu.dispatched
    assert (asked.room, asked.agent_name) == ("call_1", "pinecall-sandbox")
