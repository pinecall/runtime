"""A fleet machine's credentials at its first boot, by a join token or its cloud's secret store."""

import base64
import os
import socket
import subprocess
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

ENROLLED = "enrolled already: the credentials are sealed here"

BY_HAND = "nothing to enroll by: no join token and no cloud's secrets; a machine joined by hand"

NO_WORLD = "the machine's metadata names no pinecall-world: the instance template sets it"

NO_VERSION = "the secret {name} has no version: the box publishes it (`cell publish-secrets`)"

REFUSED = "{what} answered {status}: {detail}"

NOT_ON_GCP = "no metadata server answers here: `cell publish-secrets` runs on a box on Google Cloud"


def enroll(http: httpx.Client) -> str:
    """Seal this machine's credentials from its join token or its cloud's store; what was done."""
    if (STORE / "PINECALL_WORKER_KEY").exists():
        return ENROLLED
    if JOIN.exists():
        given = _by_token(http)
        _shredded(JOIN)
        how = "by its join token, which is spent"
    elif _metadata(http, "instance/attributes/pinecall-cloud") == "gcp":
        given = _from_secret_manager(http)
        how = "from Secret Manager, as its own service account"
    else:
        return BY_HAND
    for name, value in given.items():
        sealed(name, value)
    return f"enrolled as {_hostname()} {how}: {len(given)} credentials sealed here"


def publish(http: httpx.Client) -> list[str]:
    """Put each credential the box holds in Secret Manager when it differs; the secrets written."""
    project = _metadata(http, "project/project-id")
    if project is None:
        raise DeclarationRefused(NOT_ON_GCP)
    token = _token(http)
    written: list[str] = []
    for credential, secret, _field in CREDENTIALS:
        for world, path in _sealed_paths(credential):
            name = secret.format(world=world)
            value = _unsealed(credential, path)
            if _latest(http, token, project, name) == value:
                continue
            _add_version(http, token, project, name, value)
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


def _from_secret_manager(http: httpx.Client) -> dict[str, str]:
    world = _metadata(http, "instance/attributes/pinecall-world")
    if world is None:
        raise DeclarationRefused(NO_WORLD)
    project = _metadata(http, "project/project-id") or ""
    token = _token(http)
    found: dict[str, str] = {}
    for credential, secret, _field in CREDENTIALS:
        name = secret.format(world=world)
        value = _latest(http, token, project, name)
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


def _latest(http: httpx.Client, token: str, project: str, name: str) -> str | None:
    answer = http.get(
        f"{SECRETS_API}/projects/{project}/secrets/{name}/versions/latest:access",
        headers={"Authorization": f"Bearer {token}"},
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


def _add_version(http: httpx.Client, token: str, project: str, name: str, value: str) -> None:
    data = base64.b64encode(value.encode()).decode()
    answer = http.post(
        f"{SECRETS_API}/projects/{project}/secrets/{name}:addVersion",
        headers={"Authorization": f"Bearer {token}"},
        json={"payload": {"data": data}},
    )
    if answer.status_code != HTTPStatus.OK:
        raise UpstreamFailed(
            REFUSED.format(
                what=f"Secret Manager on {name}", status=answer.status_code, detail=_detail(answer)
            )
        )


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
