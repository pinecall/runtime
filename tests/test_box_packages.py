"""The box's manifest, read: the packages it converges on, and the units it installs by role."""

import re
import subprocess
from pathlib import Path

import pytest

from tests.tree import ROOT

pytestmark = pytest.mark.unit

BOX = ROOT / "infra" / "box"
CLOUD_INIT = BOX / "cloud-init.yaml"
MANIFEST = BOX / "Makefile"
EMBEDDER = BOX / "containers" / "pinecall-tei.container"
GATEWAY = BOX / "pinecall-gateway.service"
WORKER = BOX / "pinecall-worker.service"
DEV_STACK = ROOT / "infra" / "compose" / "dev.yml"

# What `make install` puts under Quadlet, as the manifest's own command line reads.
INTO_QUADLET = "install -D -m 644 -t /etc/containers/systemd"


def packages_cloud_init_installs() -> set[str]:
    """The `packages:` block, one `- name` per line, with any comment after the name dropped."""
    block = re.search(r"^packages:\n((?:  - .*\n)+)", CLOUD_INIT.read_text(), re.M)
    assert block is not None, "cloud-init.yaml has no packages: block"
    return {line.strip()[2:].split("#")[0].strip() for line in block.group(1).splitlines()}


def packages_the_manifest_converges() -> set[str]:
    """The PACKAGES line of the manifest, split on whitespace."""
    line = re.search(r"^PACKAGES\s*=\s*(.+)$", MANIFEST.read_text(), re.M)
    assert line is not None, "infra/box/Makefile has no PACKAGES line"
    return set(line.group(1).split())


# `make -n` prints every command a deploy would run — the recursions into embedder-<what> and
# enable-<role> included — and runs not one of them. A box reads its role and its embedder from
# /etc/pinecall/box.env; on the command line they win over that file, so each test names the box
# it is asking about rather than depending on a file this machine does not have.
def what_a_box_installs(*, role: str = "all", embed_provider: str = "tei") -> str:
    """Every command `make install` would run on a box of that role, with nothing run."""
    plan = subprocess.run(
        ["make", "-n", "install", f"ROLE={role}", f"EMBED_PROVIDER={embed_provider}"],
        cwd=MANIFEST.parent,
        capture_output=True,
        check=True,
        text=True,
    )
    return plan.stdout


def test_the_manifest_installs_exactly_what_cloud_init_installs() -> None:
    """A package added to one and not the other is a box that works from birth and never after."""
    assert packages_the_manifest_converges() == packages_cloud_init_installs()


def test_the_speech_tool_a_simulated_caller_speaks_with_is_on_the_list() -> None:
    """Without it the gateway refuses `pinecall simulate --voice` with a 503, on any box."""
    assert "espeak-ng" in packages_cloud_init_installs()


def test_a_box_that_embeds_here_installs_the_embedder_and_the_volume_its_weights_live_in() -> None:
    """The default, and what an untouched box.env means: bge-m3 served on the machine itself."""
    plan = what_a_box_installs(role="hub")
    assert "embedder-tei" in plan
    assert (
        f"{INTO_QUADLET} containers/pinecall-tei.container containers/pinecall-tei.volume" in plan
    )


def test_a_box_that_embeds_through_a_vendor_installs_none_and_stops_the_one_it_had() -> None:
    """2.3 GB of weights for a service a key already buys: the unit never reaches this box."""
    plan = what_a_box_installs(role="hub", embed_provider="perplexity")
    assert "embedder-elsewhere" in plan
    assert "systemctl stop pinecall-tei" in plan
    assert f"{INTO_QUADLET} containers/pinecall-tei" not in plan


def test_a_worker_installs_no_embedder_whatever_its_line_says() -> None:
    """A worker holds calls and nothing else: every marker is filled by the gateway, on the hub."""
    plan = what_a_box_installs(role="worker", embed_provider="tei")
    assert "embedder-elsewhere" in plan
    assert f"{INTO_QUADLET} containers/pinecall-tei" not in plan


def test_the_media_plane_every_box_runs_is_installed_whatever_it_embeds_with() -> None:
    """The embedder is the one optional member: the other four arrive on every hub, unasked."""
    plan = what_a_box_installs(role="hub", embed_provider="openrouter")
    for always in ("redis", "livekit", "sip", "postgres"):
        assert f"containers/pinecall-{always}.container" in plan


def test_the_embedder_answers_on_loopback_and_opens_no_port_to_the_world() -> None:
    """The gateway reaches it as it reaches Postgres, over `lo`: the fence gains no line for it."""
    published = re.findall(r"^PublishPort=(.+)$", EMBEDDER.read_text(), re.M)
    assert published == ["127.0.0.1:8081:80"]


def test_the_gateway_is_told_the_very_address_the_embedder_is_published_on() -> None:
    """One address, in the unit that serves it, the unit that asks and the doctor's own run."""
    published = re.search(r"^PublishPort=(\S+):(\d+):", EMBEDDER.read_text(), re.M)
    assert published is not None
    address = f"http://{published.group(1)}:{published.group(2)}"
    assert f"Environment=TEI_URL={address}" in GATEWAY.read_text()
    assert f"-E TEI_URL={address}" in MANIFEST.read_text()


def test_the_box_and_the_dev_stack_start_the_very_same_embedder() -> None:
    """One definition of what our embedder is: the same image, and the batch that fits inside it."""
    image = re.search(r"^Image=(.+)$", EMBEDDER.read_text(), re.M)
    assert image is not None
    assert image.group(1) in DEV_STACK.read_text()
    assert _arguments_the_box_starts_it_with() == _arguments_the_dev_stack_starts_it_with()


def test_the_gateway_may_read_the_key_of_either_hosted_embedder() -> None:
    """A vendor's embedder is a key like any other: in the credstore, never in a file on disk."""
    imported = _credentials_imported_by(GATEWAY)
    assert "PERPLEXITY_API_KEY" in imported
    assert "OPENROUTER_API_KEY" in imported


def test_the_worker_reads_no_embedder_key_at_all() -> None:
    """It embeds nothing, so it is handed nothing: the gateway answers every marker it asks for."""
    imported = _credentials_imported_by(WORKER)
    assert "PERPLEXITY_API_KEY" not in imported
    assert "OPENROUTER_API_KEY" not in imported


def _arguments_the_box_starts_it_with() -> list[str]:
    """The Quadlet unit's Exec=, which podman appends to the image's own entrypoint."""
    line = re.search(r"^Exec=(.+)$", EMBEDDER.read_text(), re.M)
    assert line is not None, "pinecall-tei.container has no Exec= line"
    return line.group(1).split()


def _arguments_the_dev_stack_starts_it_with() -> list[str]:
    """The `command:` of the compose stack's tei service, as the list of words it is written as."""
    written = [
        line
        for line in DEV_STACK.read_text().splitlines()
        if "command:" in line and "BAAI/bge-m3" in line
    ]
    assert len(written) == 1, "infra/compose/dev.yml has no one tei command: line"
    return re.findall(r'"([^"]+)"', written[0])


def _credentials_imported_by(unit: Path) -> list[str]:
    """Every name a unit's ImportCredential= lines ask systemd to decrypt for that process."""
    return re.findall(r"^ImportCredential=(.+)$", unit.read_text(), re.M)


# A hub that becomes a worker must give up the media plane, and `systemctl disable` refuses a
# Quadlet-generated unit before it would have stopped anything. One command for both kinds left
# four containers running on a machine that was no longer serving them.
def test_a_box_that_becomes_a_worker_stops_the_containers_it_can_no_longer_disable() -> None:
    planned = what_a_box_installs(role="worker").splitlines()
    disabled = [line for line in planned if "systemctl disable" in line]
    stopped = [line for line in planned if "systemctl stop -q" in line]
    assert all("pinecall-postgres" not in line for line in disabled)
    assert any("pinecall-postgres" in line and "pinecall-livekit" in line for line in stopped)
