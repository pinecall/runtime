"""The process: one entrypoint livekit can name, a fleet name never empty, the vendors warmed."""

from __future__ import annotations

import pickle
from functools import partial
from pathlib import Path
from typing import cast

import pytest
from livekit.agents import JobContext, JobExecutorType, JobProcess

from pinecall._settings import load_settings
from pinecall.evals import a_score
from pinecall.providers import llm, stt, tts
from pinecall.session.voice import VoiceBridge, a_bridge
from pinecall.worker import main
from pinecall.worker.entry import Worker
from pinecall.worker.load import MachineLoad, reports_no_load

pytestmark = pytest.mark.unit

A_FLEETS_KEY = "pk_a_fleets_key_nobody_will_ever_deploy"
A_MEDIA_PLANE = "wss://a-project.livekit.cloud"
A_LIVEKIT_KEY = "APIaFakeKeyForATest"
A_LIVEKIT_SECRET = "a-fake-secret-that-signs-nothing"


def test_the_fleet_name_reaches_livekit_and_is_never_empty() -> None:
    """An empty agent_name is implicit dispatch to every room in the deployment (worker.py:219)."""
    server = main.a_server(load_settings(), fleet="pinecall")
    assert server._agent_name == "pinecall"  # pyright: ignore[reportPrivateUsage]


def test_a_worker_with_no_fleet_name_is_refused_before_it_starts() -> None:
    with pytest.raises(ValueError, match="answers every room"):
        main.a_server(load_settings(), fleet="")


def test_only_one_entrypoint_is_ever_registered() -> None:
    """livekit refuses the second itself; this is the assertion that we never ask for one."""
    server = main.a_server(load_settings())
    with pytest.raises(RuntimeError, match="only one rtc_session"):
        server.rtc_session(_never_called, agent_name="second")


def test_the_vendor_tables_are_read_while_the_process_is_still_idle() -> None:
    """livekit preloads its own two models (worker.py:747-759); the vendor plugin packages are
    ours, and importing them cost a call between 1.3 s and 4.2 s until this hook took it."""
    assert main.a_server(load_settings()).setup_fnc is main.warmed


def test_warming_a_process_fills_every_modality_table() -> None:
    main.warmed(_a_job_process())
    assert "soniox" in stt.VENDORS.names
    assert "elevenlabs" in tts.VENDORS.names
    assert "anthropic" in llm.VENDORS.names


def _a_job_process() -> JobProcess:
    """livekit hands the hook the process it is warming; ours reads it for nothing."""
    return JobProcess(executor_type=JobExecutorType.PROCESS, user_arguments=None, http_proxy=None)


def test_the_entrypoint_survives_the_trip_to_a_job_process() -> None:
    """livekit pickles it by name for the spawned process (worker.py:259); a closure cannot go."""
    assert pickle.loads(pickle.dumps(main.job)) is main.job
    assert main.a_server(load_settings())._entrypoint_fnc is main.job  # pyright: ignore[reportPrivateUsage]


# The regression of 2026-09-07: the worker registered and was never sent a single availability
# request, on a laptop that was building something at the same time. livekit-server hands a job
# only to a worker whose REPORTED load is under its target_load of 0.7 (agentservice.go
# JobRequestAffinity), and dev mode turns off our own gate, never that one. Pinned on both sides
# of livekit's default so the day someone drops the parameter, this fails.
def test_a_dev_worker_reports_no_load_because_the_server_gates_on_what_it_reports() -> None:
    assert main.a_server(load_settings(), gated_by_machine_load=False).load_fnc is reports_no_load


def test_a_box_keeps_the_machines_load_because_that_is_the_backpressure_it_wants() -> None:
    """And it is watched, so the box says the moment livekit stops routing calls to it."""
    assert isinstance(main.a_server(load_settings()).load_fnc, MachineLoad)


# `worker dev` died on livekit's own ValueError beside a runtime/.env that had all three: the
# AgentServer reads os.environ (worker.py:333-335) and Settings reads the file. Only one reader.
def test_livekit_is_handed_the_url_and_the_key_pair_settings_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVEKIT_URL", A_MEDIA_PLANE)
    monkeypatch.setenv("LIVEKIT_API_KEY", A_LIVEKIT_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", A_LIVEKIT_SECRET)
    server = main.a_server(load_settings())
    assert server._ws_url == A_MEDIA_PLANE  # pyright: ignore[reportPrivateUsage]
    assert server._api_key == A_LIVEKIT_KEY  # pyright: ignore[reportPrivateUsage]
    assert server._api_secret == A_LIVEKIT_SECRET  # pyright: ignore[reportPrivateUsage]


def test_the_three_livekit_variables_are_named_the_way_an_operator_types_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What a refusal lists: the variable name, never the settings field it landed in."""
    monkeypatch.chdir(tmp_path)
    for variable in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.setenv(variable, "")
    assert main.unset_livekit_variables(load_settings()) == [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ]


def test_a_box_with_all_three_set_has_nothing_missing() -> None:
    assert main.unset_livekit_variables(load_settings()) == []


def test_the_worker_is_built_from_the_environment_the_job_process_inherited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_GATEWAY_URL", "http://gateway.internal:9000")
    monkeypatch.setenv("PINECALL_AGENT", "clinica-norte")
    built = main.a_worker(load_settings())
    # The bridge is built with the judge in hand: the session judges nothing by itself.
    bridging = cast("partial[VoiceBridge]", built.bridging)
    assert bridging.func is a_bridge
    assert bridging.keywords == {"score": a_score}
    assert built.default_agent == "clinica-norte"
    assert str(built.gateway._http.base_url) == "http://gateway.internal:9000"  # pyright: ignore[reportPrivateUsage]
    assert built.keeping("CA_7") is not None


def test_a_job_that_names_nothing_has_no_default_agent_unless_the_box_names_one() -> None:
    assert main.a_worker(load_settings()).default_agent is None


def test_the_worker_claims_the_app_socket_the_environment_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pinecall talk` exports PINECALL_APP so its own process serves the call it starts."""
    monkeypatch.setenv("PINECALL_APP", "app_7c1e")
    assert main.a_worker(load_settings()).app == "app_7c1e"


def test_a_worker_on_a_box_claims_no_app_socket_and_takes_the_newest_holder() -> None:
    assert main.a_worker(load_settings()).app is None


# The box's posture: PINECALL_API_KEY holds the key `pinecall-runtime keys issue` printed, and
# there is no dev key, because a gateway that reads one opens no database at all. Before this, the
# worker knocked with the dev key alone and a deployed one therefore sent `Bearer ""`.
def test_the_worker_knocks_with_the_fleets_own_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINECALL_API_KEY", A_FLEETS_KEY)
    monkeypatch.delenv("PINECALL_DEV_KEY", raising=False)
    assert _the_bearer_of(main.a_worker(load_settings())) == f"Bearer {A_FLEETS_KEY}"


def test_a_laptop_with_both_keys_still_knocks_with_the_deliberate_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The api key was issued on purpose; a dev key left in a shell was not."""
    monkeypatch.setenv("PINECALL_API_KEY", A_FLEETS_KEY)
    monkeypatch.setenv("PINECALL_DEV_KEY", "a-dev-key-left-over-in-this-shell")
    assert _the_bearer_of(main.a_worker(load_settings())) == f"Bearer {A_FLEETS_KEY}"


def test_a_laptop_with_only_a_dev_key_still_knocks_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clone runs the gateway before Postgres exists, and its worker has to reach that gateway."""
    monkeypatch.delenv("PINECALL_API_KEY", raising=False)
    monkeypatch.setenv("PINECALL_DEV_KEY", "the-dev-key")
    assert _the_bearer_of(main.a_worker(load_settings())) == "Bearer the-dev-key"


def _the_bearer_of(built: Worker) -> str | None:
    """What the worker's client puts on every request. Reaching into it IS the assertion."""
    http = built.gateway._http  # pyright: ignore[reportPrivateUsage]
    return http.headers.get("authorization")


async def _never_called(ctx: JobContext) -> None:
    """A second entrypoint, only ever offered to livekit so that it can refuse it."""
    raise AssertionError(ctx)
