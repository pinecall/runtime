"""A fleet machine's credentials at its first boot, by a join token or its cloud's secret store."""

import base64
import json
import os
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

import httpx

from pinecall.domain.errors import DeclarationRefused, UpstreamFailed

STORE = Path("/etc/credstore.encrypted")

# Written by cloud-init from what the fleet loop gave the machine (infra/fleet/first-boot).
JOIN = Path("/etc/pinecall/join.env")

# Each world's fleet key, where the box's pinecall-fleet-key@ unit sealed it.
FLEETS = Path("/etc/pinecall/fleets")

METADATA = "http://metadata.google.internal/computeMetadata/v1"

FLAVOR = {"Metadata-Flavor": "Google"}

SECRETS_API = "https://secretmanager.googleapis.com/v1"

# EC2's instance metadata, IMDSv2 alone: a token first, then the instance's tags and region.
IMDS = "http://169.254.169.254/latest"

IMDS_TTL = {"X-aws-ec2-metadata-token-ttl-seconds": "60"}

TIMEOUT_S = 10.0

# The credentials a worker's unit imports, each with the secret it is kept in on Google Cloud and
# the field of the join door's answer it comes in.
CREDENTIALS = (
    ("PINECALL_WORKER_KEY", "pinecall-fleet-key-{world}", "worker_key"),
    ("LIVEKIT_API_KEY", "livekit-api-key", "livekit_api_key"),
    ("LIVEKIT_API_SECRET", "livekit-api-secret", "livekit_api_secret"),
    ("PINECALL_S3_SECRET_ACCESS_KEY", "pinecall-s3-secret-access-key", "s3_secret_access_key"),
)

WORLDS = ("production", "sandbox")

_STORE_NAMES = {"gcp": "Secret Manager", "aws": "Secrets Manager"}

ENROLLED = "enrolled already: the credentials are sealed here"

BY_HAND = "nothing to enroll by: no join token and no cloud's secrets; a machine joined by hand"

NO_WORLD = "the machine's metadata names no pinecall-world: the instance template sets it"

NO_VERSION = "the secret {name} has no version: the box publishes it (`cell publish-secrets`)"

REFUSED = "{what} answered {status}: {detail}"

NOT_ON_A_CLOUD = (
    "no metadata server answers here: `cell publish-secrets` runs on a box on Google Cloud or AWS"
)


@dataclass(frozen=True)
class Cloud:
    """Which cloud keeps the secrets, how this machine reads its tags there, and where."""

    kind: str
    tag: Callable[[str], str | None]
    project: str = ""
    region: str = ""


def enroll(http: httpx.Client) -> str:
    """Seal this machine's credentials from its join token or its cloud's store; what was done."""
    if (STORE / "PINECALL_WORKER_KEY").exists():
        return ENROLLED
    if JOIN.exists():
        given = _by_token(http)
        _shredded(JOIN)
        how = "by its join token, which is spent"
    else:
        cloud = _cloud(http)
        if cloud is None or cloud.tag("pinecall-cloud") != cloud.kind:
            return BY_HAND
        given = _from_the_store(http, cloud)
        how = f"from {_STORE_NAMES[cloud.kind]}, as its own identity"
    for name, value in given.items():
        sealed(name, value)
    return f"enrolled as {_hostname()} {how}: {len(given)} credentials sealed here"


def publish(http: httpx.Client) -> list[str]:
    """Put each credential the box holds in Secret Manager when it differs; the secrets written."""
    cloud = _cloud(http)
    if cloud is None:
        raise DeclarationRefused(NOT_ON_A_CLOUD)
    written: list[str] = []
    for credential, secret, _field in CREDENTIALS:
        for world, path in _sealed_paths(credential):
            name = secret.format(world=world)
            value = _unsealed(credential, path)
            if _latest(http, cloud, name) == value:
                continue
            _add_version(http, cloud, name, value)
            written.append(name)
    return written


def sealed(name: str, value: str) -> None:
    """One credential sealed into this machine's store, from a pipe: never on the disk in clear."""
    STORE.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = STORE / name
    encrypt = ("systemd-creds", "encrypt", f"--name={name}", "-", str(target))
    subprocess.run(encrypt, input=value.encode(), check=True, capture_output=True)
    target.chmod(0o600)


def _by_token(http: httpx.Client) -> dict[str, str]:
    given = _join_env(JOIN.read_text())
    answer = http.post(
        f"{given['PINECALL_JOIN_URL'].rstrip('/')}/v1/fleet/join",
        headers={"Authorization": f"Bearer {given['PINECALL_JOIN_TOKEN']}"},
        json={"worker": _hostname()},
    )
    if answer.status_code != HTTPStatus.OK:
        raise UpstreamFailed(
            REFUSED.format(what="the join door", status=answer.status_code, detail=_detail(answer))
        )
    joined = answer.json()
    return {
        credential: str(joined[field])
        for credential, _, field in CREDENTIALS
        if joined.get(field) is not None
    }


def _from_the_store(http: httpx.Client, cloud: Cloud) -> dict[str, str]:
    world = cloud.tag("pinecall-world")
    if world is None:
        raise DeclarationRefused(NO_WORLD)
    found: dict[str, str] = {}
    for credential, secret, _field in CREDENTIALS:
        name = secret.format(world=world)
        value = _latest(http, cloud, name)
        if value is None:
            raise DeclarationRefused(NO_VERSION.format(name=name))
        found[credential] = value
    return found


def _sealed_paths(credential: str) -> list[tuple[str, Path]]:
    if credential == "PINECALL_WORKER_KEY":
        return [(world, FLEETS / f"{world}.credstore" / credential) for world in WORLDS]
    return [("", STORE / credential)]


def _unsealed(name: str, path: Path) -> str:
    decrypt = ("systemd-creds", "decrypt", f"--name={name}", str(path), "-")
    return subprocess.run(decrypt, capture_output=True, check=True, text=True).stdout


# Google's on Google Cloud (the machine's token, over HTTP); AWS's through the aws CLI, which signs
# as the instance profile, a value on its stdin and never in its arguments.
def _cloud(http: httpx.Client) -> Cloud | None:
    project = _metadata(http, "project/project-id")
    if project is not None:
        return Cloud(
            kind="gcp",
            tag=lambda name: _metadata(http, f"instance/attributes/{name}"),
            project=project,
        )
    imds = _imds_token(http)
    if imds is None:
        return None
    region = _imds(http, imds, "meta-data/placement/region") or ""
    return Cloud(
        kind="aws",
        tag=lambda name: _imds(http, imds, f"meta-data/tags/instance/{name}"),
        region=region,
    )


def _latest(http: httpx.Client, cloud: Cloud, name: str) -> str | None:
    if cloud.kind == "aws":
        return _aws_secret(cloud, "get-secret-value", name, None)
    answer = http.get(
        f"{SECRETS_API}/projects/{cloud.project}/secrets/{name}/versions/latest:access",
        headers={"Authorization": f"Bearer {_token(http)}"},
    )
    if answer.status_code == HTTPStatus.NOT_FOUND:
        return None
    if answer.status_code != HTTPStatus.OK:
        raise UpstreamFailed(
            REFUSED.format(
                what=f"Secret Manager on {name}", status=answer.status_code, detail=_detail(answer)
            )
        )
    return base64.b64decode(answer.json()["payload"]["data"]).decode()


def _add_version(http: httpx.Client, cloud: Cloud, name: str, value: str) -> None:
    if cloud.kind == "aws":
        _aws_secret(cloud, "put-secret-value", name, value)
        return
    data = base64.b64encode(value.encode()).decode()
    answer = http.post(
        f"{SECRETS_API}/projects/{cloud.project}/secrets/{name}:addVersion",
        headers={"Authorization": f"Bearer {_token(http)}"},
        json={"payload": {"data": data}},
    )
    if answer.status_code != HTTPStatus.OK:
        raise UpstreamFailed(
            REFUSED.format(
                what=f"Secret Manager on {name}", status=answer.status_code, detail=_detail(answer)
            )
        )


# A secret with no value yet is AWS's ResourceNotFoundException, as a missing one is.
def _aws_secret(cloud: Cloud, verb: str, name: str, value: str | None) -> str | None:
    argv = ["aws", "secretsmanager", verb, "--region", cloud.region, "--secret-id", name]
    if value is not None:
        argv += ["--secret-string", "file:///dev/stdin"]
    stdin = None if value is None else value
    done = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        if "ResourceNotFoundException" in done.stderr:
            return None
        detail = done.stderr.strip()[-200:]
        raise UpstreamFailed(
            REFUSED.format(what=f"Secrets Manager on {name}", status=done.returncode, detail=detail)
        )
    return None if value is not None else str(json.loads(done.stdout)["SecretString"])


def _imds_token(http: httpx.Client) -> str | None:
    try:
        answer = http.put(f"{IMDS}/api/token", headers=IMDS_TTL, timeout=2.0)
    except httpx.TransportError:
        return None
    return answer.text.strip() if answer.status_code == HTTPStatus.OK else None


def _imds(http: httpx.Client, token: str, path: str) -> str | None:
    answer = http.get(f"{IMDS}/{path}", headers={"X-aws-ec2-metadata-token": token}, timeout=2.0)
    return answer.text.strip() if answer.status_code == HTTPStatus.OK else None


def _metadata(http: httpx.Client, path: str) -> str | None:
    try:
        answer = http.get(f"{METADATA}/{path}", headers=FLAVOR, timeout=2.0)
    except httpx.TransportError:
        return None
    return answer.text.strip() if answer.status_code == HTTPStatus.OK else None


def _token(http: httpx.Client) -> str:
    answer = http.get(f"{METADATA}/instance/service-accounts/default/token", headers=FLAVOR)
    if answer.status_code != HTTPStatus.OK:
        raise UpstreamFailed(
            REFUSED.format(
                what="the metadata server's token",
                status=answer.status_code,
                detail=_detail(answer),
            )
        )
    return str(answer.json()["access_token"])


def _join_env(text: str) -> dict[str, str]:
    pairs = (line.partition("=") for line in text.splitlines() if "=" in line)
    return {key.strip(): value.strip() for key, _, value in pairs}


# The token is written over before it goes, so no block of the disk keeps it.
def _shredded(path: Path) -> None:
    size = path.stat().st_size
    with path.open("r+b") as opened:
        opened.write(os.urandom(size))
        opened.flush()
        os.fsync(opened.fileno())
    path.unlink()


def _hostname() -> str:
    return socket.gethostname().split(".")[0]


# A refusal's own words, never a credential: the doors answer {"detail": …}.
def _detail(answer: httpx.Response) -> str:
    try:
        return str(answer.json().get("detail") or answer.json().get("error", {}).get("message"))
    except ValueError:
        return answer.text[:200]
