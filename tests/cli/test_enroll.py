"""Tests for a fleet machine's credentials at its first boot, and the box's publishing of them."""

import base64
import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from pinecall.cli import _enroll
from pinecall.cli._enroll import BY_HAND, ENROLLED, enroll, publish
from pinecall.domain.errors import DeclarationRefused

PROJECT = "a-project"

SEALED = "sealed:"

# What a box holds, by the credential's name: fake values, never a real key's shape.
HELD = {
    "PINECALL_WORKER_KEY": "fleet-key-of-the-world",
    "LIVEKIT_API_KEY": "livekit-key",
    "LIVEKIT_API_SECRET": "livekit-secret",
    "PINECALL_S3_SECRET_ACCESS_KEY": "store-secret",
}

IN_SECRET_MANAGER = {
    "pinecall-fleet-key-production": "fleet-key-of-the-world",
    "livekit-api-key": "livekit-key",
    "livekit-api-secret": "livekit-secret",
    "pinecall-s3-secret-access-key": "store-secret",
}


class Creds:
    """systemd-creds as the machine runs it, and the aws CLI's Secrets Manager beside it."""

    def __init__(self) -> None:
        """Nothing put in Secrets Manager yet."""
        self.put: dict[str, str] = {}

    def run(self, argv: Sequence[str], **given: object) -> subprocess.CompletedProcess[str]:
        """Seal what comes on stdin, answer what a sealed path holds, or read and put a secret."""
        if argv[0] == "aws":
            return self.secrets(argv, given.get("input"))
        if argv[1] == "encrypt":
            data = given["input"]
            assert isinstance(data, bytes)
            Path(argv[-1]).write_text(SEALED + data.decode())
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        kept = Path(argv[-2]).read_text().removeprefix(SEALED)
        return subprocess.CompletedProcess(argv, 0, stdout=kept, stderr="")

    def secrets(self, argv: Sequence[str], value: object) -> subprocess.CompletedProcess[str]:
        """Read or put a secret as the aws CLI does, the value on stdin alone."""
        name = argv[argv.index("--secret-id") + 1]
        assert argv[argv.index("--region") + 1] == "us-east-1"
        if argv[2] == "put-secret-value":
            assert argv[-1] == "file:///dev/stdin"
            assert isinstance(value, str)
            self.put[name] = value
            return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
        kept = IN_SECRET_MANAGER.get(name)
        if kept is None:
            missing = "An error occurred (ResourceNotFoundException)"
            return subprocess.CompletedProcess(argv, 254, stdout="", stderr=missing)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"SecretString": kept}))


def on_this_machine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, creds: Creds | None = None
) -> Path:
    """The machine's store, join file and fleets under the test's directory; its name, w-7."""
    store = tmp_path / "credstore"
    monkeypatch.setattr(_enroll, "STORE", store)
    monkeypatch.setattr(_enroll, "JOIN", tmp_path / "join.env")
    monkeypatch.setattr(_enroll, "FLEETS", tmp_path / "fleets")
    monkeypatch.setattr(_enroll.subprocess, "run", (creds or Creds()).run)
    monkeypatch.setattr(_enroll.socket, "gethostname", lambda: "w-7.c.project.internal")
    return store


def answered(text: str) -> httpx.Response:
    """A metadata server's plain answer."""
    return httpx.Response(200, text=text)


def accessed(value: str) -> httpx.Response:
    """Secret Manager's answer to a version's access."""
    data = base64.b64encode(value.encode()).decode()
    return httpx.Response(200, json={"payload": {"data": data}})


def a_cloud(
    added: dict[str, str], *, metadata: dict[str, str], tags: dict[str, str] | None = None
) -> httpx.Client:
    """Google's metadata server and Secret Manager, versions added recorded; EC2's IMDS on AWS."""

    def answer(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(_enroll.IMDS):
            return an_ec2(request, tags)
        if url.startswith(_enroll.METADATA):
            assert request.headers["Metadata-Flavor"] == "Google"
            path = url.removeprefix(f"{_enroll.METADATA}/")
            if path == "instance/service-accounts/default/token":
                return httpx.Response(200, json={"access_token": "a-token"})
            return answered(metadata[path]) if path in metadata else httpx.Response(404)
        assert request.headers["Authorization"] == "Bearer a-token"
        name = re.split(r"[/:]", url.split("/secrets/", maxsplit=1)[1], maxsplit=1)[0]
        if url.endswith(":addVersion"):
            data = json.loads(request.content)["payload"]["data"]
            added[name] = base64.b64decode(data).decode()
            return httpx.Response(200, json={})
        value = IN_SECRET_MANAGER.get(name)
        return accessed(value) if value is not None else httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(answer))


def an_ec2(request: httpx.Request, tags: dict[str, str] | None) -> httpx.Response:
    """EC2's IMDSv2: a token for a PUT, then the instance's tags and region with that token."""
    if tags is None:
        raise httpx.ConnectError("no IMDS off EC2", request=request)
    if request.method == "PUT":
        assert request.headers["X-aws-ec2-metadata-token-ttl-seconds"] == "60"
        return answered("an-imds-token")
    assert request.headers["X-aws-ec2-metadata-token"] == "an-imds-token"
    path = str(request.url).removeprefix(f"{_enroll.IMDS}/meta-data/")
    known = {"placement/region": "us-east-1"} | {f"tags/instance/{k}": v for k, v in tags.items()}
    return answered(known[path]) if path in known else httpx.Response(404)


EC2 = {"pinecall-cloud": "aws", "pinecall-world": "production"}

GCP = {
    "instance/attributes/pinecall-cloud": "gcp",
    "instance/attributes/pinecall-world": "production",
    "project/project-id": PROJECT,
}


def test_a_join_token_is_spent_for_the_credentials_and_shredded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = on_this_machine(monkeypatch, tmp_path)
    (tmp_path / "join.env").write_text(
        "PINECALL_JOIN_URL=https://box.example\nPINECALL_JOIN_TOKEN=a-join-token\n"
    )

    def door(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://box.example/v1/fleet/join"
        assert request.headers["Authorization"] == "Bearer a-join-token"
        assert json.loads(request.content) == {"worker": "w-7"}
        return httpx.Response(
            200,
            json={
                "fleet": "pinecall",
                "worker_key": HELD["PINECALL_WORKER_KEY"],
                "livekit_api_key": HELD["LIVEKIT_API_KEY"],
                "livekit_api_secret": HELD["LIVEKIT_API_SECRET"],
                "s3_secret_access_key": None,
            },
        )

    done = enroll(httpx.Client(transport=httpx.MockTransport(door)))
    assert done.startswith("enrolled as w-7 by its join token")
    assert not (tmp_path / "join.env").exists()
    assert sorted(path.name for path in store.iterdir()) == [
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "PINECALL_WORKER_KEY",
    ]
    assert (store / "LIVEKIT_API_SECRET").read_text() == SEALED + "livekit-secret"


def test_a_google_cloud_machine_reads_its_world_s_secrets_as_its_own_service_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = on_this_machine(monkeypatch, tmp_path)
    done = enroll(a_cloud({}, metadata=GCP))
    assert done.startswith("enrolled as w-7 from Secret Manager")
    assert {path.name: path.read_text() for path in store.iterdir()} == {
        name: SEALED + value for name, value in HELD.items()
    }


def test_a_machine_enrolled_already_or_joined_by_hand_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = on_this_machine(monkeypatch, tmp_path)
    assert enroll(a_cloud({}, metadata={})) == BY_HAND
    store.mkdir()
    (store / "PINECALL_WORKER_KEY").write_text(SEALED + "kept")
    assert enroll(a_cloud({}, metadata=GCP)) == ENROLLED
    assert (store / "PINECALL_WORKER_KEY").read_text() == SEALED + "kept"


def test_a_secret_the_box_never_published_is_said_and_nothing_is_sealed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = on_this_machine(monkeypatch, tmp_path)
    monkeypatch.delitem(IN_SECRET_MANAGER, "livekit-api-secret")
    with pytest.raises(DeclarationRefused, match="livekit-api-secret has no version"):
        enroll(a_cloud({}, metadata=GCP))
    assert not store.exists()


def test_the_box_publishes_what_differs_and_leaves_what_is_current(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = on_this_machine(monkeypatch, tmp_path)
    store.mkdir()
    for name in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "PINECALL_S3_SECRET_ACCESS_KEY"):
        (store / name).write_text(SEALED + HELD[name])
    for world, key in (("production", HELD["PINECALL_WORKER_KEY"]), ("sandbox", "sandbox-key")):
        fleet = tmp_path / "fleets" / f"{world}.credstore"
        fleet.mkdir(parents=True)
        (fleet / "PINECALL_WORKER_KEY").write_text(SEALED + key)
    monkeypatch.setitem(IN_SECRET_MANAGER, "livekit-api-key", "an-older-livekit-key")
    added: dict[str, str] = {}
    written = publish(a_cloud(added, metadata=GCP))
    assert sorted(written) == ["livekit-api-key", "pinecall-fleet-key-sandbox"]
    assert added == {"livekit-api-key": "livekit-key", "pinecall-fleet-key-sandbox": "sandbox-key"}


def test_publishing_off_google_cloud_is_refused_in_words(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    on_this_machine(monkeypatch, tmp_path)
    with pytest.raises(DeclarationRefused, match="on Google Cloud or AWS"):
        publish(a_cloud({}, metadata={}))


def test_an_aws_machine_reads_its_world_s_secrets_as_its_instance_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = on_this_machine(monkeypatch, tmp_path)
    done = enroll(a_cloud({}, metadata={}, tags=EC2))
    assert done.startswith("enrolled as w-7 from Secrets Manager")
    assert {path.name: path.read_text() for path in store.iterdir()} == {
        name: SEALED + value for name, value in HELD.items()
    }


def test_an_aws_box_puts_what_differs_on_the_cli_s_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    creds = Creds()
    store = on_this_machine(monkeypatch, tmp_path, creds)
    store.mkdir()
    for name in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "PINECALL_S3_SECRET_ACCESS_KEY"):
        (store / name).write_text(SEALED + HELD[name])
    for world, key in (("production", HELD["PINECALL_WORKER_KEY"]), ("sandbox", "sandbox-key")):
        fleet = tmp_path / "fleets" / f"{world}.credstore"
        fleet.mkdir(parents=True)
        (fleet / "PINECALL_WORKER_KEY").write_text(SEALED + key)
    written = publish(a_cloud({}, metadata={}, tags={"pinecall-cloud": "aws"}))
    assert written == ["pinecall-fleet-key-sandbox"]
    assert creds.put == {"pinecall-fleet-key-sandbox": "sandbox-key"}
