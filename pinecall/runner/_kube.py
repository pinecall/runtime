"""The cluster as the runner drives it: one gVisor pod a host, over the Kubernetes API."""

import hashlib
import re
import ssl
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from pinecall.domain.errors import UpstreamFailed
from pinecall.domain.names import Json, JsonObject

# Every pod the runner starts carries these, so a runner that restarts finds what is its own. Two
# runners, one per world, share the namespace: each lists and touches only its world's.
WORLD_LABEL = "pinecall.io/world"


# A label's value takes no `/`: the app is its stamp in the label, its name in an annotation.
APP_LABEL = "pinecall.io/app"


APP_ANNOTATION = "pinecall.io/app"


RELEASE_LABEL = "pinecall.io/release"


# The spike's measure (72 MB an idle agent under gVisor) with room for a tool's burst.
MEMORY = "256Mi"


CPU = "500m"


# The install runs the org's own scripts: the same sandbox, more room, and a clock.
INSTALL_MEMORY = "1Gi"


INSTALL_WITHIN_S = 300


# The one writable place an app has besides its install: TMPDIR points here, a tmpfs.
SCRATCH = "/scratch"


SCRATCH_SIZE = "64Mi"


# Public resolvers: nothing of the cluster's is an app's to ask.
RESOLVERS = ("1.1.1.1", "8.8.8.8")


# What a drain needs: ten seconds for the gateway, thirty for the slowest tool.
DRAIN_S = 45


A_VERB_WITHIN_S = 30.0


# Where a container finds what it is started with: a secret the runner wrote, mounted read-only.
ENVIRONMENT = "/run/pinecall"


# The user every container of an org's code runs as: never root, the same in both containers.
APP_USER = 1000


IN_CLUSTER = "https://kubernetes.default.svc"


NOT_A_VARIABLE = "{name!r} is not an environment variable's name"


# What deleting a pod or a secret already gone answers: it is gone, as asked.
NOT_THERE = 404


# What creating a host's secret answers when its pod went with a node and the secret stayed.
ALREADY_THERE = 409


REFUSED = "the cluster {verb}: {status} {detail}"


NO_ANSWER = "the cluster {verb}: no answer in {seconds:.0f}s"


# The release's sources, fetched from this world's runner by their digest, then its dependencies
# installed the way its lockfile says; npm resolves what package.json names when there is none.
INSTALL = (
    "node -e 'fetch(process.argv[1]).then(async (r) => { if (!r.ok) throw new Error(r.status);"
    ' process.stdout.write(Buffer.from(await r.arrayBuffer())); })\' "$PINECALL_SOURCE"'
    " | tar -xz -C /app && cd /app && "
    "if [ -f pnpm-lock.yaml ]; then corepack pnpm install --frozen-lockfile --prod; "
    "elif [ -f package-lock.json ]; then npm ci --omit=dev --no-audit --no-fund; "
    "else npm install --omit=dev --no-audit --no-fund; fi"
)


# Where a pod's service account finds what it knocks the cluster's API with.
SERVICE_ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")


# What a pod whose process is over is in; a pod still installing or starting has not exited.
ENDED = frozenset({"Succeeded", "Failed"})


A_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Engine:
    """Where every app runs: its image, its runtime class, its namespace, where sources are."""

    image: str
    runtime_class: str
    namespace: str
    # This runner's own address as an app's pod reaches it: `<url>/sources/<sha256>`.
    sources_url: str


@dataclass(frozen=True)
class Container:
    """A pod the runner started: its name (the host), its app and release, how it is."""

    name: str
    app: str
    release: int
    state: str
    started_at: float

    @property
    def has_exited(self) -> bool:
        """Whether its process ended; one still installing or starting has not."""
        return self.state in ENDED


@dataclass(frozen=True)
class Launch:
    """One host to start: whose, which release and its digest, what it starts with."""

    world: str
    app: str
    host: str
    release: int
    sha256: str
    command: Sequence[str]


class _Read(BaseModel):
    """What a listing says, the fields read of it and nothing else."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class _Running(_Read):
    started_at: str | None = Field(None, alias="startedAt")


class _State(_Read):
    running: _Running | None = None


class _ContainerStatus(_Read):
    name: str
    state: _State = Field(default_factory=_State)


class _Status(_Read):
    phase: str = "Pending"
    containers: list[_ContainerStatus] = Field(
        default_factory=list[_ContainerStatus], alias="containerStatuses"
    )


class _Metadata(_Read):
    name: str
    labels: dict[str, str] = Field(default_factory=dict[str, str])
    annotations: dict[str, str] = Field(default_factory=dict[str, str])
    created: str = Field("", alias="creationTimestamp")


class _Pod(_Read):
    metadata: _Metadata
    status: _Status = Field(default_factory=_Status)


@dataclass(frozen=True)
class _Knock:
    """One request to the cluster's API: what it is called in a refusal, and what it sends."""

    verb: str
    method: str
    path: str
    body: JsonObject | None = None
    params: Mapping[str, str] | None = None


@dataclass
class Cluster:
    """The cluster's API, knocked as the runner's own service account."""

    http: httpx.AsyncClient
    engine: Engine
    token: Path = SERVICE_ACCOUNT / "token"

    async def pods(self, world: str) -> list[Container]:
        """Every pod this world's runner started, running or not; never another world's."""
        selector = {"labelSelector": f"{APP_LABEL},{WORLD_LABEL}={world}"}
        listing = await self._required(_Knock("list", "GET", self._at("pods"), params=selector))
        return containers_in(listing.json().get("items", []))

    async def start(self, launch: Launch, environment: Mapping[str, str]) -> None:
        """The host's environment as a secret, then its pod: installing first, then running."""
        secret = environment_secret(launch.host, environment)
        made = await self._request(_Knock("create", "POST", self._at("secrets"), body=secret))
        if made.status_code == ALREADY_THERE:
            path = self._at(f"secrets/{_secret_of(launch.host)}")
            await self._required(_Knock("replace", "PUT", path, body=secret))
        elif not made.is_success:
            raise UpstreamFailed(_refusal("create", made))
        await self._required(
            _Knock("create", "POST", self._at("pods"), body=pod(self.engine, launch))
        )

    async def stop(self, name: str, grace_s: int = DRAIN_S) -> None:
        """SIGTERM, then grace_s (the drain's, unless said) before the kill; its environment too."""
        body: JsonObject = {"gracePeriodSeconds": grace_s}
        for path in (f"pods/{name}", f"secrets/{_secret_of(name)}"):
            answer = await self._request(_Knock("delete", "DELETE", self._at(path), body=body))
            if not answer.is_success and answer.status_code != NOT_THERE:
                raise UpstreamFailed(_refusal("delete", answer))

    async def logs(self, name: str, lines: int) -> str:
        """The last lines the host printed; its install's, when it never got to run."""
        for container in ("app", "install"):
            query = {"container": container, "tailLines": str(lines)}
            answer = await self._request(
                _Knock("logs", "GET", self._at(f"pods/{name}/log"), params=query)
            )
            if answer.is_success and answer.text.strip():
                return answer.text
        return ""

    async def _required(self, knock: _Knock) -> httpx.Response:
        answer = await self._request(knock)
        if not answer.is_success:
            raise UpstreamFailed(_refusal(knock.verb, answer))
        return answer

    # The token is read on every knock: the kubelet rotates it under the runner.
    async def _request(self, knock: _Knock) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token.read_text().strip()}"}
        try:
            return await self.http.request(
                knock.method,
                knock.path,
                headers=headers,
                json=knock.body,
                params=knock.params,
                timeout=A_VERB_WITHIN_S,
            )
        except httpx.TimeoutException:
            raise UpstreamFailed(
                NO_ANSWER.format(verb=knock.verb, seconds=A_VERB_WITHIN_S)
            ) from None
        except httpx.HTTPError as failed:
            raise UpstreamFailed(f"the cluster {knock.verb}: {failed}") from failed

    def _at(self, kind: str) -> str:
        return f"/api/v1/namespaces/{self.engine.namespace}/{kind}"


# The process's own shell reads its environment, then becomes the command.
READ_THEN_RUN = f'. {ENVIRONMENT}/env && exec "$@"'


_PODS: TypeAdapter[list[_Pod]] = TypeAdapter(list[_Pod])


def in_cluster(engine: Engine) -> Cluster:
    """The cluster's API as this pod's service account knocks it, trusting the cluster's CA."""
    verified = ssl.create_default_context(cafile=str(SERVICE_ACCOUNT / "ca.crt"))
    return Cluster(http=httpx.AsyncClient(base_url=IN_CLUSTER, verify=verified), engine=engine)


def app_stamp(app: str) -> str:
    """The app as a label's value takes it: a stamp of `org/name`."""
    return hashlib.sha256(app.encode()).hexdigest()[:16]


# Single-quoted, so any value is itself: a key of several lines, a quote, a dollar sign.
def exported(environment: Mapping[str, str]) -> str:
    """The environment as a shell reads it: one `export NAME='value'` a variable."""
    lines: list[str] = []
    for name, value in sorted(environment.items()):
        if not A_VARIABLE.match(name):
            raise UpstreamFailed(NOT_A_VARIABLE.format(name=name))
        quoted = value.replace("'", "'\\''")
        lines.append(f"export {name}='{quoted}'")
    return "\n".join(lines) + "\n"


def environment_secret(host: str, environment: Mapping[str, str]) -> JsonObject:
    """The host's environment as a secret: one file, `env`, the shell's to read."""
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": _secret_of(host)},
        "type": "Opaque",
        "stringData": {"env": exported(environment)},
    }


def pod(engine: Engine, launch: Launch) -> JsonObject:
    """One host: gVisor, no token, public resolvers, installed by its first container, then run."""
    source = f"{engine.sources_url.rstrip('/')}/sources/{launch.sha256}"
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": launch.host,
            "labels": {
                APP_LABEL: app_stamp(launch.app),
                WORLD_LABEL: launch.world,
                RELEASE_LABEL: str(launch.release),
            },
            "annotations": {APP_ANNOTATION: launch.app},
        },
        "spec": {
            "runtimeClassName": engine.runtime_class,
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "terminationGracePeriodSeconds": DRAIN_S,
            "hostname": launch.host[:63],
            "dnsPolicy": "None",
            "dnsConfig": {"nameservers": list(RESOLVERS)},
            "securityContext": {
                "runAsNonRoot": True,
                "fsGroup": APP_USER,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "volumes": [
                {"name": "app", "emptyDir": {"sizeLimit": "1Gi"}},
                {"name": "home", "emptyDir": {"sizeLimit": "1Gi"}},
                {"name": "scratch", "emptyDir": {"medium": "Memory", "sizeLimit": SCRATCH_SIZE}},
                {
                    "name": "environment",
                    "secret": {"secretName": _secret_of(launch.host), "defaultMode": 0o444},
                },
            ],
            "initContainers": [
                {
                    "name": "install",
                    "image": engine.image,
                    "command": ["timeout", str(INSTALL_WITHIN_S), "sh", "-c", INSTALL],
                    "env": [
                        {"name": "HOME", "value": "/home/app"},
                        {"name": "TMPDIR", "value": SCRATCH},
                        {"name": "PINECALL_SOURCE", "value": source},
                    ],
                    "securityContext": _locked(),
                    "resources": _capped(INSTALL_MEMORY),
                    "volumeMounts": [
                        {"name": "app", "mountPath": "/app"},
                        {"name": "home", "mountPath": "/home/app"},
                        {"name": "scratch", "mountPath": SCRATCH},
                    ],
                }
            ],
            "containers": [
                {
                    "name": "app",
                    "image": engine.image,
                    "command": ["sh", "-c", READ_THEN_RUN, "sh", *launch.command],
                    "workingDir": "/app",
                    "env": [
                        {"name": "HOME", "value": SCRATCH},
                        {"name": "TMPDIR", "value": SCRATCH},
                        {"name": "NO_COLOR", "value": "1"},
                    ],
                    "securityContext": _locked(),
                    "resources": _capped(MEMORY),
                    "volumeMounts": [
                        {"name": "app", "mountPath": "/app", "readOnly": True},
                        {"name": "scratch", "mountPath": SCRATCH},
                        {"name": "environment", "mountPath": ENVIRONMENT, "readOnly": True},
                    ],
                }
            ],
        },
    }


# A pod that has not got to run yet is given the install's time before its registering is timed.
def containers_in(items: list[Json]) -> list[Container]:
    """The pods a listing answered, as the runner's plan reads them."""
    found: list[Container] = []
    for item in _PODS.validate_python(items):
        running = next(
            (
                each.state.running.started_at
                for each in item.status.containers
                if each.name == "app" and each.state.running is not None
            ),
            None,
        )
        started = (
            _seconds(running) if running else _seconds(item.metadata.created) + INSTALL_WITHIN_S
        )
        found.append(
            Container(
                name=item.metadata.name,
                app=item.metadata.annotations.get(APP_ANNOTATION, ""),
                release=int(item.metadata.labels.get(RELEASE_LABEL, "0") or 0),
                state=item.status.phase,
                started_at=started,
            )
        )
    return found


def _secret_of(host: str) -> str:
    """The secret a host's environment is kept in."""
    return f"{host}-env"


# What every container of an org's code runs under: no privilege, no root, nothing writable but
# its own scratch.
def _locked() -> JsonObject:
    return {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
        "runAsUser": APP_USER,
        "runAsGroup": APP_USER,
        "capabilities": {"drop": ["ALL"]},
    }


def _capped(memory: str) -> JsonObject:
    caps: JsonObject = {"memory": memory, "cpu": CPU}
    return {"requests": caps, "limits": caps}


def _seconds(stamp: str) -> float:
    if not stamp:
        return time.time()
    return datetime.fromisoformat(stamp).timestamp()


def _refusal(verb: str, answer: httpx.Response) -> str:
    return REFUSED.format(verb=verb, status=answer.status_code, detail=answer.text[:500])
