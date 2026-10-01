"""Tests for the runner's podman verbs: the sandbox every org's container runs in."""

from pathlib import Path

import pytest

from pinecall.domain.errors import UpstreamFailed
from pinecall.runner._podman import (
    Engine,
    Launch,
    containers_in,
    exported,
    install_argv,
    listing_argv,
    network_argv,
    prune_networks_argv,
    ran,
    run_argv,
)

ENGINE = Engine(image="docker.io/library/node:24-slim", runtime="runsc")


def launch(folder: Path) -> Launch:
    return Launch(
        world="production",
        app="org_1/support",
        host="support-r1-abcdef12",
        release=1,
        network="pinecall-net",
        folder=folder,
        environment=folder / "env",
        command=["./node_modules/.bin/pinecall", "start", "--prod"],
    )


def test_a_release_runs_sandboxed_read_only_capped_and_on_its_own_network(tmp_path: Path) -> None:
    argv = run_argv(ENGINE, launch(tmp_path))
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
        "--label=pinecall.release=1",
    ):
        assert flag in argv
    assert argv[-3:] == ["./node_modules/.bin/pinecall", "start", "--prod"]


# An org's secret is read by the container's own shell off a file, never by podman: a name like
# LD_PRELOAD in podman's environment, which is root's, would be root's to run.
def test_the_environment_is_a_file_the_container_reads_and_podman_never_sees(
    tmp_path: Path,
) -> None:
    argv = run_argv(ENGINE, launch(tmp_path))
    assert f"--volume={tmp_path / 'env'}:/run/pinecall:ro" in argv
    assert not any(part.startswith("--env=") and "PINECALL_KEY" in part for part in argv)
    assert argv[argv.index("sh") + 2] == '. /run/pinecall/env && exec "$@"'


def test_the_environment_is_exported_so_that_any_value_is_itself() -> None:
    written = exported({"KEY": "it's $HOME\nand more", "A": "1"})
    assert written == "export A='1'\nexport KEY='it'\\''s $HOME\nand more'\n"
    with pytest.raises(UpstreamFailed, match="not an environment variable"):
        exported({"A B": "1"})


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
    argv = install_argv(ENGINE, tmp_path, "pinecall-net", tmp_path / "scratch")
    assert argv[-1] == install
    assert f"--volume={tmp_path / 'scratch'}:/scratch:U" in argv
    assert "--env=HOME=/scratch" in argv
    assert "--runtime=runsc" in argv
    assert f"--volume={tmp_path}:/app:U" in argv


def test_podmans_listing_is_read_for_the_name_the_app_and_the_state() -> None:
    listing = """[
      {"Names": ["support-r1-abcdef12"], "Labels": {"pinecall.app": "org_1/support"},
       "State": "running", "Id": "x", "StartedAt": 1700000000},
      {"Names": ["support-r2-abcdef12"], "State": "exited",
       "Labels": {"pinecall.app": "org_1/support", "pinecall.release": "2"}},
      {"Names": [], "Labels": null, "State": "exited"}
    ]"""
    first, second = containers_in(listing)
    assert (first.name, first.app, first.release, first.has_exited, first.started_at) == (
        "support-r1-abcdef12",
        "org_1/support",
        1,
        False,
        1700000000.0,
    )
    assert (second.release, second.has_exited) == (2, True)
    assert containers_in("") == []


async def test_a_verb_is_run_and_what_it_printed_comes_back() -> None:
    done = await ran(["sh", "-c", "echo out; echo err >&2; exit 3"])
    assert done.returncode == 3
    assert done.output.split() == ["out", "err"]


async def test_a_verb_past_its_time_is_killed_and_said() -> None:
    with pytest.raises(UpstreamFailed, match="no answer in 0s"):
        await ran(["sleep", "5"], within_s=0.2)


def test_an_apps_network_is_on_a_bridge_the_fence_knows_by_its_name() -> None:
    argv = network_argv("pinecall-0123456789ab", "pca0123456789ab", "production")
    assert "--interface-name=pca0123456789ab" in argv
    assert "--disable-dns" in argv
    assert "--label=pinecall.world=production" in argv
    assert len("pca0123456789ab") <= 15


def test_a_runner_lists_labels_and_prunes_only_its_own_worlds(tmp_path: Path) -> None:
    assert "--label=pinecall.world=production" in run_argv(ENGINE, launch(tmp_path))
    assert "--filter=label=pinecall.world=sandbox" in listing_argv("sandbox")
    assert "--filter=label=pinecall.world=sandbox" in prune_networks_argv("sandbox")
