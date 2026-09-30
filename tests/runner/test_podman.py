"""Tests for the runner's podman verbs: the sandbox every org's container runs in."""

from pathlib import Path

import pytest

from pinecall.domain.errors import UpstreamFailed
from pinecall.runner._podman import (
    Engine,
    Launch,
    containers_in,
    install_argv,
    network_argv,
    ran,
    run_argv,
)

ENGINE = Engine(image="docker.io/library/node:24-slim", runtime="runsc")


def launch(folder: Path) -> Launch:
    return Launch(
        app="org_1/support",
        host="support-r1-abcdef12",
        network="pinecall-net",
        folder=folder,
        command=["./node_modules/.bin/pinecall", "start", "--prod"],
    )


def test_a_release_runs_sandboxed_read_only_capped_and_on_its_own_network(tmp_path: Path) -> None:
    argv = run_argv(ENGINE, launch(tmp_path), ["CRM_TOKEN", "PINECALL_KEY"])
    for flag in (
        "--runtime=runsc",
        "--userns=auto",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--network=pinecall-net",
        "--memory=256m",
        "--memory-swap=256m",
        "--hostname=support-r1-abcdef12",
        "--dns=1.1.1.1",
        f"--volume={tmp_path}:/app:ro",
    ):
        assert flag in argv
    assert argv[-3:] == ["./node_modules/.bin/pinecall", "start", "--prod"]


def test_a_secrets_value_is_never_in_the_argv_only_its_name(tmp_path: Path) -> None:
    argv = run_argv(ENGINE, launch(tmp_path), ["CRM_TOKEN"])
    assert "--env=CRM_TOKEN" in argv
    assert not any(part.startswith("--env=CRM_TOKEN=") for part in argv)


@pytest.mark.parametrize(
    ("lockfile", "install"),
    [
        ("pnpm-lock.yaml", "corepack pnpm install --frozen-lockfile --prod"),
        ("package-lock.json", "npm ci --omit=dev --no-audit --no-fund"),
        (None, "npm install --omit=dev --no-audit --no-fund"),
    ],
)
def test_the_lockfile_picks_the_install(tmp_path: Path, lockfile: str | None, install: str) -> None:
    if lockfile is not None:
        (tmp_path / lockfile).write_text("")
    argv = install_argv(ENGINE, tmp_path, "pinecall-net")
    assert argv[-1] == install
    assert "--runtime=runsc" in argv
    assert f"--volume={tmp_path}:/app:U" in argv


def test_podmans_listing_is_read_for_the_name_the_app_and_the_state() -> None:
    listing = """[
      {"Names": ["support-r1-abcdef12"], "Labels": {"pinecall.app": "org_1/support"},
       "State": "running", "Id": "x"},
      {"Names": [], "Labels": null, "State": "exited"}
    ]"""
    [container] = containers_in(listing)
    assert (container.name, container.app, container.is_running) == (
        "support-r1-abcdef12",
        "org_1/support",
        True,
    )
    assert containers_in("") == []


async def test_a_verb_is_run_and_what_it_printed_comes_back() -> None:
    done = await ran(["sh", "-c", "echo out; echo err >&2; exit 3"])
    assert done.returncode == 3
    assert done.output.split() == ["out", "err"]


async def test_a_verb_past_its_time_is_killed_and_said() -> None:
    with pytest.raises(UpstreamFailed, match="no answer in 0s"):
        await ran(["sleep", "5"], within_s=0.2)


def test_an_apps_network_is_on_a_bridge_the_fence_knows_by_its_name() -> None:
    argv = network_argv("pinecall-0123456789ab", "pca0123456789ab")
    assert "--interface-name=pca0123456789ab" in argv
    assert len("pca0123456789ab") <= 15
