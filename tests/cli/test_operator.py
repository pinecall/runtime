"""Tests for the operator's verbs, knocked on a real gateway with the box's own key."""

import asyncio
import json
from pathlib import Path

import pytest

from pinecall.channels.telephony.carrier import TWILIO_SIGNALLING
from pinecall.cli.main import main, verbs
from pinecall.domain.errors import GatewayRefused, PinecallError
from pinecall.process.settings import Settings
from tests.conftest import AGENT, BOX_DOMAIN, FLEETS, Knocking, postgres
from tests.gateway.api.test_ops import THE_OPS_KEY, with_an_ops_key


def settings_of(knocking: Knocking, *, ops_key: str | None = THE_OPS_KEY) -> Settings:
    """The settings the operator types with: the gateway's address and the box's key."""
    with_an_ops_key(knocking)
    return Settings.model_validate(
        {"PINECALL_GATEWAY_URL": knocking.url, **({"PINECALL_OPS_KEY": ops_key} if ops_key else {})}
    )


# Off the loop: the verbs knock with a blocking client, and the gateway answers on this loop.
async def ran(settings: Settings, *argv: str) -> int:
    """A verb parsed as the terminal would and run on these settings."""
    parsed = verbs().parse_args(list(argv))
    return await asyncio.to_thread(lambda: int(parsed.run(settings, parsed)))


@postgres
async def test_init_makes_the_org_seats_the_first_admin_and_hands_them_the_box(
    knocking: Knocking, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = settings_of(knocking)
    assert (
        await ran(settings, "init", "--org", "tienda", "--email", "you@t.test", "--person", "You")
        == 0
    )
    printed = capsys.readouterr().out
    assert "tienda" in printed.splitlines()[0]
    assert "runs this box" in printed
    # The box's public name, never the loopback address the verb knocked at.
    assert f"https://{BOX_DOMAIN}/invitations/inv_" in printed
    assert f"pinecall login https://{BOX_DOMAIN}" in printed
    assert (
        await ran(settings, "init", "--org", "tienda", "--email", "you@t.test", "--person", "You")
        == 0
    )
    assert "already there" in capsys.readouterr().out


@postgres
async def test_orgs_are_added_listed_limited_and_removed_from_the_terminal(
    knocking: Knocking, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = settings_of(knocking)
    assert await ran(settings, "orgs", "add", "tienda", "--name", "La Tienda") == 0
    assert await ran(settings, "orgs", "list") == 0
    assert "tienda" in capsys.readouterr().out
    assert (
        await ran(
            settings,
            "orgs",
            "quota",
            "tienda",
            "--env",
            "sandbox",
            "--minutes",
            "30",
            "--lends",
            "acme",
        )
        == 0
    )
    quoted = capsys.readouterr().out
    assert '"minutes": 30' in quoted
    assert '"acme"' in quoted
    assert await ran(settings, "orgs", "dialling", "tienda", "--per-minute", "2") == 0
    assert '"per_minute": 2' in capsys.readouterr().out
    assert await ran(settings, "orgs", "sso", "tienda") == 0
    assert "no identity provider" in capsys.readouterr().out
    assert await ran(settings, "orgs", "rm", "tienda") == 0
    with pytest.raises(GatewayRefused, match="404"):
        await ran(settings, "orgs", "rm", "tienda")


@postgres
async def test_a_person_is_invited_made_operator_and_removed_by_their_address(
    knocking: Knocking, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = settings_of(knocking)
    org = knocking.org.id
    assert await ran(settings, "orgs", "invite", org, "ana@clinica.test", "--name", "Ana") == 0
    assert f"https://{BOX_DOMAIN}/invitations/inv_" in capsys.readouterr().out
    assert await ran(settings, "orgs", "operator", org, "ana@clinica.test") == 0
    assert "runs this box" in capsys.readouterr().out
    assert await ran(settings, "orgs", "operator", org, "ana@clinica.test", "--revoke") == 0
    assert "no longer" in capsys.readouterr().out
    assert await ran(settings, "orgs", "remove-member", org, "ana@clinica.test") == 0
    with pytest.raises(GatewayRefused, match="nobody"):
        await ran(settings, "orgs", "remove-member", org, "ana@clinica.test")


@postgres
async def test_keys_are_issued_once_listed_by_fingerprint_and_revoked(
    knocking: Knocking, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = settings_of(knocking)
    org = knocking.org.id
    assert (
        await ran(settings, "keys", "issue", "--org", org, "--env", "sandbox", "--label", "ci") == 0
    )
    issued = capsys.readouterr().out.splitlines()
    assert issued[0].startswith("pc_test_")
    assert "never shown again" in issued[-1]
    assert await ran(settings, "keys", "list", "--org", org) == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if "  ci  " in line]
    assert [line.split()[-1] for line in rows] == ["live"]
    fingerprint = rows[0].split()[0]
    assert await ran(settings, "keys", "revoke", fingerprint) == 0
    assert await ran(settings, "keys", "list", "--org", org) == 0
    assert any(
        line.startswith(fingerprint) and line.endswith("revoked")
        for line in capsys.readouterr().out.splitlines()
    )


@postgres
async def test_routes_are_typed_listed_seeded_and_forgotten(
    knocking: Knocking, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    settings = settings_of(knocking)
    org = knocking.org.id
    assert await ran(settings, "routes", "add", "+59829001199", AGENT, "--org", org) == 0
    seed = tmp_path / "routes.json"
    row = {
        "org": org,
        "number": "+59829001188",
        "agent": AGENT,
        "channel": "phone",
        "env": "sandbox",
    }
    seed.write_text(json.dumps([row]))
    assert await ran(settings, "routes", "seed", "--file", str(seed)) == 0
    assert await ran(settings, "routes", "list", "--org", org) == 0
    assert "+59829001199" in capsys.readouterr().out
    assert await ran(settings, "routes", "list", "--org", org, "--env", "sandbox") == 0
    assert "+59829001188" in capsys.readouterr().out
    assert await ran(settings, "routes", "rm", "+59829001199", "--org", org) == 0


@postgres
async def test_the_fleet_is_listed_and_a_worker_cordoned_and_let_be(
    knocking: Knocking, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = settings_of(knocking)
    beat = {
        "fleet": FLEETS["sandbox"],
        "worker": "pinecall-worker-1",
        "active": 4,
        "max_jobs": 4,
        # What a worker of four slots holding four reports: full at every slot, not at 0.7.
        "load": 1.0,
        "draining": False,
    }
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/fleet/heartbeat", json=beat)
    assert await ran(settings, "fleet", "list") == 0
    listed = capsys.readouterr().out
    assert "pinecall-worker-1" in listed
    assert "full" in listed
    assert await ran(settings, "fleet", "cordon", "pinecall-worker-1") == 0
    assert await ran(settings, "fleet", "uncordon", "pinecall-worker-1") == 0


@postgres
async def test_the_fence_is_exported_as_terraforms_sip_sources_twilio_first(
    knocking: Knocking, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = settings_of(knocking)
    assert await ran(settings, "fence", "export") == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported == {"sip_sources": list(TWILIO_SIGNALLING)}


@postgres
async def test_a_box_whose_sip_is_at_its_names_sends_no_trunk_on(
    knocking: Knocking, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await ran(settings_of(knocking), "sip", "repoint") == 0
    assert capsys.readouterr().out.strip().endswith("0 trunks sent on")
    former = ("sip", "repoint", "--from", "production=sip.gone.test")
    assert await ran(settings_of(knocking), *former) == 0
    assert capsys.readouterr().out.strip().endswith("0 trunks sent on")


@postgres
async def test_without_an_ops_key_the_operators_verbs_say_so(knocking: Knocking) -> None:
    settings = Settings.model_validate({"PINECALL_GATEWAY_URL": knocking.url})
    with pytest.raises(PinecallError, match="PINECALL_OPS_KEY"):
        await ran(settings, "orgs", "list")


def test_every_operator_group_is_a_verb_of_the_terminal() -> None:
    for group in ("init", "orgs", "keys", "routes", "fleet", "sessions", "providers", "memory"):
        with pytest.raises(SystemExit) as usage:
            main([group, "--help"])
        assert usage.value.code == 0
