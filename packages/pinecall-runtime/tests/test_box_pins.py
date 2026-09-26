"""What the box takes on trust is pinned, and what a process may touch is said in a drop-in."""

import re
from pathlib import Path

import pytest

from tests.support.tree import ROOT
from tests.test_box_packages import BOX, CLOUD_INIT, DEV_STACK, MANIFEST, what_a_box_installs

pytestmark = pytest.mark.unit

DEPLOY = ROOT / "Makefile"
DOCKERFILE = ROOT / "infra" / "compose" / "postgres" / "Dockerfile"
HARDENING = BOX / "hardening.conf"
A_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
A_FINGERPRINT = re.compile(r"fpr:\*([0-9A-F]{40}):")
A_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")

# The units that run for the life of the box as the runtime's own user, each given the drop-in.
LONG_RUNNING = ("pinecall-gateway@", "pinecall-worker@", "pinecall-overflow", "pinecall-fleet")
# The oneshots that catch a key in /run, and the one unit that runs podman as root: not hardened,
# and the drop-in's header says why.
NEVER_HARDENED = ("pinecall-worker-key@", "pinecall-operator-key", "pinecall-db@")


def test_every_long_running_unit_gets_the_hardening_drop_in_and_the_oneshots_do_not() -> None:
    plan = what_a_box_installs(role="all")
    for unit in LONG_RUNNING:
        assert f"hardening.conf /etc/systemd/system/{unit}.service.d/hardening.conf" in plan, unit
    for unit in NEVER_HARDENED:
        assert f"{unit}.service.d" not in plan, unit


def test_the_drop_in_says_the_six_things_systemd_analyze_security_asks_first() -> None:
    said = _directives(HARDENING)
    assert said["NoNewPrivileges"] == "yes"
    assert said["ProtectSystem"] == "strict"
    assert said["ProtectHome"] == "yes"
    assert said["PrivateTmp"] == "yes"
    assert "AF_INET" in said["RestrictAddressFamilies"]
    assert said["SystemCallFilter"] == "@system-service"
    assert said["CapabilityBoundingSet"] == ""


def test_a_hardened_unit_names_what_it_writes_and_backs_off_when_it_cannot_start() -> None:
    """Under ProtectSystem=strict a path not named is read-only, silently."""
    for unit in LONG_RUNNING:
        said = _directives(BOX / f"{unit}.service")
        assert said["ReadWritePaths"], unit
        assert said["RestartSteps"] and said["RestartMaxDelaySec"], unit


def test_the_worker_and_the_gateway_may_write_the_recordings_and_the_plugins_cache() -> None:
    for unit in ("pinecall-gateway@", "pinecall-worker@"):
        paths = _directives(BOX / f"{unit}.service")["ReadWritePaths"].split()
        assert "/var/lib/pinecall/recordings" in paths and "/opt/pinecall/.cache" in paths, unit


def test_a_tenants_app_keeps_a_lighter_fence_and_opt_stays_writable() -> None:
    said = _directives(BOX / "pinecall-app@.service")
    assert said["ProtectSystem"] == "full" and said["NoNewPrivileges"] == "yes"
    assert "pinecall-app@.service.d" not in what_a_box_installs(role="all")


def test_the_journal_is_bounded_and_journald_restarted_only_when_the_bounds_changed() -> None:
    bounds = _directives(BOX / "journald.conf.d" / "pinecall.conf")
    assert bounds["SystemMaxUse"] and bounds["SystemKeepFree"] and bounds["MaxRetentionSec"]
    plan = what_a_box_installs(role="all")
    assert "cmp -s journald.conf.d/pinecall.conf /etc/systemd/journald.conf.d/pinecall.conf" in plan
    assert "systemctl restart systemd-journald" in plan


def test_the_deploy_compiles_bytecode_because_the_units_cannot_write_it() -> None:
    assert "--compile-bytecode" in DEPLOY.read_text()


# ── what travels ─────────────────────────────────────────────────────────────


def test_rsync_leaves_the_notebook_the_recordings_and_every_env_file_at_home() -> None:
    rsync = re.search(r"^RSYNC = (.*?)(?=\n\S)", DEPLOY.read_text(), re.MULTILINE | re.DOTALL)
    assert rsync is not None
    excluded = re.findall(r"--exclude '?([^\s']+)'?", rsync.group(1))
    for kept_home in ("docs/decisions", "recordings", ".env", ".env.*", "deploy.local.mk", ".git"):
        assert kept_home in excluded, kept_home
    # The built console is git-ignored and MUST travel: nothing here names it.
    assert not any("console" in name or "gateway" in name for name in excluded)


# ── what is pinned ───────────────────────────────────────────────────────────


def test_every_image_from_a_registry_is_named_by_tag_and_digest() -> None:
    for unit in (BOX / "containers").glob("*.container"):
        image = _directives(unit)["Image"]
        assert image.startswith("localhost/") or A_DIGEST.search(image), unit.name
    for image in re.findall(r"^\s*image: (.+)$", DEV_STACK.read_text(), re.MULTILINE):
        reference = image.strip('"').removeprefix("${TEI_IMAGE:-").removesuffix("}")
        assert reference.startswith("pinecall/postgres") or A_DIGEST.search(reference), image
    postgres = re.search(r"^ARG POSTGRES_IMAGE=(.+)$", DOCKERFILE.read_text(), re.MULTILINE)
    assert postgres is not None and A_DIGEST.search(postgres.group(1))


def test_the_digest_is_the_indexs_and_never_one_architectures() -> None:
    """The one place a digest is read for the tree asks for the index media types first."""
    script = (ROOT / "scripts" / "image-digests").read_text()
    assert "application/vnd.oci.image.index.v1+json" in script
    assert "manifest.list.v2+json" in script


def test_uv_arrives_as_a_release_held_to_its_checksum_and_never_as_curl_pipe_sh() -> None:
    cloud_init = CLOUD_INIT.read_text()
    assert "install.sh" not in cloud_init
    uv = re.search(r"releases/download/(\d+\.\d+\.\d+)/\$build\.tar\.gz", cloud_init)
    assert uv is not None, "cloud-init names no uv release"
    assert "sha256sum -c" in cloud_init
    assert len(A_SHA256.findall(cloud_init)) >= 2, "one checksum per architecture"


def test_hcloud_arrives_held_to_its_checksum_per_architecture() -> None:
    manifest = MANIFEST.read_text()
    for arch in ("amd64", "arm64"):
        assert re.search(rf"^HCLOUD_SHA256_{arch}\s*=\s*[0-9a-f]{{64}}$", manifest, re.MULTILINE)
    assert "sha256sum -c --quiet && tar -xzf $$file" in manifest


def test_the_nodesource_key_is_held_to_one_fingerprint_at_birth_and_on_every_deploy() -> None:
    at_birth = A_FINGERPRINT.findall(CLOUD_INIT.read_text())
    on_deploy = re.search(
        r"^NODE_FINGERPRINT\s*=\s*([0-9A-F]{40})$", MANIFEST.read_text(), re.MULTILINE
    )
    assert on_deploy is not None
    assert at_birth == [on_deploy.group(1)]
    assert "$(NODE_FINGERPRINT):'" in MANIFEST.read_text()


def _directives(unit: Path) -> dict[str, str]:
    """Every `Key=value` line of a unit or drop-in, the last one winning as systemd reads them."""
    said: dict[str, str] = {}
    for line in unit.read_text().splitlines():
        if line.startswith(("#", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        said[key.strip()] = value.strip()
    return said
