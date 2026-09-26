"""The box's manifest, read: the packages it converges on, its instances, and its units by role."""

import re
import subprocess
from pathlib import Path

import pytest

from tests.tree import ROOT

pytestmark = pytest.mark.unit

BOX = ROOT / "infra" / "box"
CLOUD_INIT = BOX / "cloud-init.yaml"
MANIFEST = BOX / "Makefile"
FENCE = BOX / "nftables.conf"
EMBEDDER = BOX / "containers" / "pinecall-tei.container"
GATEWAY = BOX / "pinecall-gateway@.service"
WORKER = BOX / "pinecall-worker@.service"
# The units every instance has one of, enabled once per name box.env lists.
TEMPLATES = [
    BOX / f"pinecall-{name}@.service" for name in ("db", "gateway", "worker-key", "worker")
]
DEV_STACK = ROOT / "infra" / "compose" / "dev.yml"

# What `make install` puts under Quadlet, as the manifest's own command line reads.
INTO_QUADLET = "install -D -m 644 -t /etc/containers/systemd"


def packages_cloud_init_installs() -> set[str]:
    """The `packages:` block, one `- name` per line, with any comment after the name dropped."""
    block = re.search(r"^packages:\n((?:  - .*\n)+)", CLOUD_INIT.read_text(), re.MULTILINE)
    assert block is not None, "cloud-init.yaml has no packages: block"
    return {line.strip()[2:].split("#")[0].strip() for line in block.group(1).splitlines()}


def packages_the_manifest_converges() -> set[str]:
    """The PACKAGES line of the manifest, split on whitespace."""
    line = re.search(r"^PACKAGES\s*=\s*(.+)$", MANIFEST.read_text(), re.MULTILINE)
    assert line is not None, "infra/box/Makefile has no PACKAGES line"
    return set(line.group(1).split())


# `make -n` prints every command a deploy would run — both halves, the recursions into
# embedder-<what> and enable-<role> included — and runs not one of them. A box reads its role, its
# embedder and its instances from /etc/pinecall/box.env; on the command line they win over that
# file, so each test names the box it is asking about rather than depending on a file this machine
# does not have.
def what_a_box_installs(
    *,
    role: str = "all",
    embed_provider: str = "tei",
    fleet_cloud: str = "",
    instances: str = "production",
) -> str:
    """Every command `make install converge` would run on a box of that role, with nothing run."""
    plan = subprocess.run(
        [
            "make",
            "-n",
            "install",
            "converge",
            f"ROLE={role}",
            f"EMBED_PROVIDER={embed_provider}",
            f"FLEET_CLOUD={fleet_cloud}",
            f"INSTANCES={instances}",
        ],
        cwd=MANIFEST.parent,
        capture_output=True,
        check=True,
        text=True,
    )
    return plan.stdout


def test_the_manifest_installs_exactly_what_cloud_init_installs() -> None:
    """A package added to one and not the other is a box that works from birth and never after."""
    assert packages_the_manifest_converges() == packages_cloud_init_installs()


def test_a_tenants_app_can_be_held_on_the_box() -> None:
    """The template unit is installed with the rest, and the Node it runs on converges."""
    plan = what_a_box_installs(role="all")
    assert "pinecall-app@.service" in plan
    assert "nodejs" in packages_cloud_init_installs()
    assert "nodesource" in plan, "NodeSource's repository goes in before apt is asked for nodejs"
    assert "pnpm@" in plan
    template = (BOX / "pinecall-app@.service").read_text()
    assert 'PINECALL_KEY=$$(cat "$CREDENTIALS_DIRECTORY/pinecall-app-%i.key")' in template
    assert "PINECALL_URL=http://127.0.0.1:8080" in template
    assert "pinecall start --prod" in template
    assert 'set -a; . "$CREDENTIALS_DIRECTORY/pinecall-app-%i.env"' in template
    # The manager reads environment files before the credentials exist: measured, 2026-09-16.
    assert not re.search(r"^EnvironmentFile=", template, re.MULTILINE)


def test_the_fence_lets_the_sip_containers_own_answers_out() -> None:
    """livekit-sip answers from the bridge, to the carrier's 5060: the fence must see that before
    it drops 5060 from anyone but the carrier, or every call rings for ever (2026-09-16)."""
    fence = FENCE.read_text()
    bridge = fence.index('iifname "podman*" meta l4proto { tcp, udp } th dport 5060 accept')
    drop = fence.index('th dport 5060 counter drop comment "5060 from anyone but the carrier"')
    assert bridge < drop


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
    """A worker holds calls and nothing else: every lookup is run by the gateway, on the hub."""
    plan = what_a_box_installs(role="worker", embed_provider="tei")
    assert "embedder-elsewhere" in plan
    assert f"{INTO_QUADLET} containers/pinecall-tei" not in plan


def test_the_media_plane_every_box_runs_is_installed_whatever_it_embeds_with() -> None:
    """The embedder is the one optional member: the other four arrive on every hub, unasked."""
    plan = what_a_box_installs(role="hub", embed_provider="openrouter")
    for always in ("redis", "livekit", "sip", "postgres", "egress"):
        assert f"containers/pinecall-{always}.container" in plan


def test_the_embedder_answers_on_loopback_and_opens_no_port_to_the_world() -> None:
    """The gateway reaches it as it reaches Postgres, over `lo`: the fence gains no line for it."""
    published = re.findall(r"^PublishPort=(.+)$", EMBEDDER.read_text(), re.MULTILINE)
    assert published == ["127.0.0.1:8081:80"]


def test_the_gateway_is_told_the_very_address_the_embedder_is_published_on() -> None:
    """One address, in the unit that serves it, the unit that asks and the doctor's own run."""
    published = re.search(r"^PublishPort=(\S+):(\d+):", EMBEDDER.read_text(), re.MULTILINE)
    assert published is not None
    address = f"http://{published.group(1)}:{published.group(2)}"
    assert f"Environment=TEI_URL={address}" in GATEWAY.read_text()
    assert f"-E TEI_URL={address}" in MANIFEST.read_text()


def test_the_box_and_the_dev_stack_start_the_very_same_embedder() -> None:
    """One definition of what our embedder is: the same image, and the batch that fits inside it."""
    image = re.search(r"^Image=(.+)$", EMBEDDER.read_text(), re.MULTILINE)
    assert image is not None
    assert image.group(1) in DEV_STACK.read_text()
    assert _arguments_the_box_starts_it_with() == _arguments_the_dev_stack_starts_it_with()


def test_the_gateway_may_read_the_key_of_either_hosted_embedder() -> None:
    """A vendor's embedder is a key like any other: in the credstore, never in a file on disk."""
    imported = _credentials_imported_by(GATEWAY)
    assert "PERPLEXITY_API_KEY" in imported
    assert "OPENROUTER_API_KEY" in imported


def test_the_worker_reads_no_embedder_key_at_all() -> None:
    """It embeds nothing, so it is handed nothing: the gateway runs every lookup it asks for."""
    imported = _credentials_imported_by(WORKER)
    assert "PERPLEXITY_API_KEY" not in imported
    assert "OPENROUTER_API_KEY" not in imported


def _arguments_the_box_starts_it_with() -> list[str]:
    """The Quadlet unit's Exec=, which podman appends to the image's own entrypoint."""
    line = re.search(r"^Exec=(.+)$", EMBEDDER.read_text(), re.MULTILINE)
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
    return re.findall(r"^ImportCredential=(.+)$", unit.read_text(), re.MULTILINE)


# A hub that becomes a worker must give up the media plane, and `systemctl disable` refuses a
# Quadlet-generated unit before it would have stopped anything. One command for both kinds left
# four containers running on a machine that was no longer serving them.
def test_a_box_that_becomes_a_worker_stops_the_containers_it_can_no_longer_disable() -> None:
    planned = what_a_box_installs(role="worker").splitlines()
    disabled = [line for line in planned if "systemctl disable" in line]
    stopped = [line for line in planned if "systemctl stop -q" in line]
    assert all("pinecall-postgres" not in line for line in disabled)
    assert any("pinecall-postgres" in line and "pinecall-livekit" in line for line in stopped)
    # The recorder with them: a worker box records nothing, because the room it would compose is
    # on the hub, where livekit is.
    assert any("pinecall-egress" in line for line in stopped)


# The two halves of one decision, and they are in two files: the group the recorder writes as, and
# the group the directory it writes into belongs to. A recording nobody can read is what a
# disagreement here looks like, and it looks like that a week later, on a call somebody asks for.
def test_the_recorder_writes_as_the_group_the_recordings_directory_belongs_to() -> None:
    recorder = (BOX / "containers" / "pinecall-egress.container").read_text()
    group = re.search(r"^PodmanArgs=--group-add (\d+)$", recorder, re.MULTILINE)
    assert group is not None
    declared = re.search(
        r"^g\s+pinecall-media\s+(\d+)",
        (BOX / "sysusers.d" / "pinecall.conf").read_text(),
        re.MULTILINE,
    )
    assert declared is not None and declared.group(1) == group.group(1)
    kept = (BOX / "tmpfiles.d" / "pinecall.conf").read_text()
    # setgid, so what the recorder writes stays in the group the gateway reads as.
    assert re.search(
        r"^d\s+/var/lib/pinecall/recordings\s+2770\s+pinecall\s+pinecall-media", kept, re.MULTILINE
    )


def test_the_recorder_answers_on_loopback_and_opens_no_port_to_the_world() -> None:
    """Nothing knocks at it but the doctor: a recording is asked for through livekit itself."""
    recorder = (BOX / "containers" / "pinecall-egress.container").read_text()
    assert re.findall(r"^PublishPort=(.+)$", recorder, re.MULTILINE) == ["127.0.0.1:7980:7980"]


# The overflow agent lives where the media plane is and never counts as a seat: a hub and a full
# box enable it, a worker does not. The loop is enabled only on a hub whose box.env names a cloud.
def test_the_overflow_agent_is_the_hubs_and_never_a_workers() -> None:
    for role in ("all", "hub"):
        enabled = [
            line
            for line in what_a_box_installs(role=role).splitlines()
            if "systemctl enable" in line
        ]
        assert any("pinecall-overflow" in line for line in enabled), role
    worker = what_a_box_installs(role="worker")
    assert "enable -q nftables pinecall-worker@production" in worker
    assert "disable -q --now" in worker and "pinecall-overflow" in worker


def test_the_fleet_loop_is_enabled_only_when_box_env_names_a_cloud() -> None:
    quiet = what_a_box_installs(role="hub")
    assert not any("enable" in line and "pinecall-fleet" in line for line in quiet.splitlines())
    assert "disable -q --now pinecall-fleet" in quiet
    looping = what_a_box_installs(role="hub", fleet_cloud="gcp")
    assert any("enable" in line and "pinecall-fleet" in line for line in looping.splitlines())


def test_a_cordoned_worker_stays_down() -> None:
    """Exit 3 is the worker leaving on purpose (worker/heartbeat.py); systemd leaves it down."""
    assert "RestartPreventExitStatus=3" in WORKER.read_text()


def test_a_hub_that_names_a_cloud_installs_that_clouds_own_cli_and_never_the_snap() -> None:
    """snapd refuses a service user homed under /opt; the vendor's package is what the loop runs."""
    quiet = what_a_box_installs(role="hub")
    assert "google-cloud-cli" not in quiet
    looping = what_a_box_installs(role="hub", fleet_cloud="gcp")
    assert "google-cloud-cli" in looping
    assert "/snap/bin" not in (BOX / "pinecall-fleet.service").read_text()


# ── instances ────────────────────────────────────────────────────────────────────


def _enabled(plan: str) -> str:
    """The one `systemctl enable` line of a plan: what the role turns on at boot."""
    (line,) = [line for line in plan.splitlines() if line.startswith("systemctl enable -q")]
    return line


def test_every_instance_box_env_lists_gets_its_own_units_and_its_own_site() -> None:
    plan = what_a_box_installs(role="all", instances="production sandbox")
    enabled = _enabled(plan)
    for unit in ("db", "gateway", "worker-key", "worker"):
        for name in ("production", "sandbox"):
            assert f"pinecall-{unit}@{name}" in enabled
    assert "for name in production sandbox; do" in plan  # one Caddy site each
    assert "ready-production ready-sandbox" in plan  # each made whole before anything is enabled


def test_a_hub_enables_no_instances_worker_and_a_worker_no_instances_gateway() -> None:
    hub = what_a_box_installs(role="hub", instances="production sandbox")
    assert "pinecall-worker@sandbox" not in _enabled(hub)
    assert "disable -q --now pinecall-worker@production pinecall-worker@sandbox" in hub
    worker = what_a_box_installs(role="worker", instances="production sandbox")
    assert _enabled(worker) == (
        "systemctl enable -q nftables pinecall-worker@production pinecall-worker@sandbox"
    )


def test_nothing_about_any_one_instance_is_written_in_a_template() -> None:
    """The sandbox is the instance whose env file says so, and never a line of a unit file."""
    for template in TEMPLATES:
        said = _without_comments(template.read_text())
        for instance_word in ("production", "sandbox", ":8080", ":8180", "PINECALL_GATEWAY_URL="):
            assert instance_word not in said, f"{template.name}: {instance_word}"
        assert "EnvironmentFile=/etc/pinecall/instances/%i.env" in said


def test_no_unit_imports_the_pinecall_glob_that_would_hand_it_another_instances_keys() -> None:
    for unit in BOX.glob("*.service"):
        assert "ImportCredential=PINECALL_*" not in unit.read_text(), unit.name


def test_an_instances_own_secrets_are_loaded_by_path_and_the_rest_by_name() -> None:
    """One list: what the units load out of an instance's store is what the manifest checks and
    copies, and not one of those names is ever imported from the box's shared store."""
    own = set(_make_variable("INSTANCE_SECRETS").split())
    loaded: set[str] = set()
    for unit in BOX.glob("*.service"):
        text = unit.read_text()
        for credential, path in re.findall(
            r"^LoadCredentialEncrypted=([^:]+):(\S+)$", text, re.MULTILINE
        ):
            if path.startswith("/etc/pinecall/instances/"):
                assert path.endswith(f".credstore/{credential}"), f"{unit.name}: {credential}"
                loaded.add(credential)
        assert not own & set(_credentials_imported_by(unit)), unit.name
    assert loaded == own


def test_an_instance_draws_every_secret_of_its_own_but_the_one_its_worker_key_unit_mints() -> None:
    from pinecall.cli.box.verbs import instance_secrets

    minted = re.search(r"--name=(\w+) ", (BOX / "pinecall-worker-key@.service").read_text())
    assert minted is not None
    assert set(instance_secrets("sandbox")) | {minted.group(1)} == set(
        _make_variable("INSTANCE_SECRETS").split()
    )


# A peer key is loaded by a drop-in the manifest writes from what the store holds, never by the
# template: a path that is missing fails the start, and most instances hold no peer key at all.
def test_a_gateway_loads_the_peer_keys_its_store_holds_and_no_template_names_one() -> None:
    from pinecall.cli.box.peer import PEER_SECRETS

    assert _make_variable("PEER_SECRETS").split() == list(PEER_SECRETS)
    for unit in BOX.glob("*.service"):
        assert not set(PEER_SECRETS) & set(re.findall(r"=(PINECALL_\w+):", unit.read_text()))
    plan = what_a_box_installs(role="hub", instances="production sandbox")
    assert "box peer --among production sandbox" in plan
    assert "pinecall-gateway@$name.service.d" in plan


def test_a_worker_box_mints_no_peer_key_it_has_no_gateway_to_load_it_into() -> None:
    assert "box peer" not in what_a_box_installs(role="worker", instances="production sandbox")


def test_the_caddy_snippet_proxies_each_site_to_its_own_port_and_the_sfu_to_the_one() -> None:
    caddyfile = (BOX / "caddy" / "Caddyfile").read_text()
    assert "reverse_proxy 127.0.0.1:{args[0]}" in caddyfile
    assert "reverse_proxy 127.0.0.1:7880" in caddyfile
    assert "PINECALL_DOMAIN" not in _without_comments(caddyfile)
    assert "import /etc/caddy/instances/*.caddy" in caddyfile
    plan = what_a_box_installs(role="all")
    assert "import pinecall %s" in plan
    assert "rm -f /etc/caddy/conf.d/sandbox.caddy" in plan


def test_the_single_units_the_templates_replaced_are_retired_not_left_enabled() -> None:
    plan = what_a_box_installs(role="all")
    assert "systemctl disable -q pinecall-gateway pinecall-worker pinecall-worker-key" in plan
    for unit in ("pinecall-gateway", "pinecall-worker", "pinecall-worker-key"):
        assert not (BOX / f"{unit}.service").exists()


def _make_variable(name: str) -> str:
    """One `NAME = value` line of the manifest, as written."""
    line = re.search(rf"^{name}\s*=\s*(.+)$", MANIFEST.read_text(), re.MULTILINE)
    assert line is not None, f"infra/box/Makefile has no {name} line"
    return line.group(1)


def _without_comments(unit: str) -> str:
    """A unit file or Caddyfile with its comment lines taken out: what systemd or Caddy reads."""
    return "\n".join(line for line in unit.splitlines() if not line.lstrip().startswith("#"))
