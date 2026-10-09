"""Tests for the runner's cluster: the pod every org's code runs in, and the verbs on it."""

from pathlib import Path

import httpx
import pytest

from pinecall.domain.errors import UpstreamFailed
from pinecall.domain.names import Json, JsonObject
from pinecall.runner._kube import (
    APP_LABEL,
    RELEASE_LABEL,
    SCRATCH,
    WORLD_LABEL,
    Cluster,
    Engine,
    Launch,
    app_stamp,
    containers_in,
    environment_secret,
    exported,
    pod,
)

ENGINE = Engine(
    image="docker.io/library/node:24-slim",
    runtime_class="gvisor",
    namespace="pinecall-apps",
    sources_url="http://runner-production.pinecall-runner.svc:8080/",
)

LAUNCH = Launch(
    world="production",
    app="org_1/support",
    host="support-r1-abcdef12",
    release=1,
    sha256="ab" * 32,
    command=["/opt/pinecall/bin/pinecall", "start", "--prod"],
)

SOURCE = "http://runner-production.pinecall-runner.svc:8080/sources/" + "ab" * 32


def an_object(value: Json) -> JsonObject:
    assert isinstance(value, dict)
    return value


def a_list(value: Json) -> list[Json]:
    assert isinstance(value, list)
    return value


def containers_of(made: JsonObject, kind: str) -> list[JsonObject]:
    return [an_object(each) for each in a_list(an_object(made["spec"])[kind])]


def test_a_release_runs_under_gvisor_with_no_token_no_root_and_public_resolvers() -> None:
    made = pod(ENGINE, LAUNCH)
    spec = an_object(made["spec"])
    assert spec["runtimeClassName"] == "gvisor"
    assert spec["automountServiceAccountToken"] is False
    assert spec["enableServiceLinks"] is False
    assert spec["restartPolicy"] == "Never"
    assert spec["dnsPolicy"] == "None"
    assert spec["dnsConfig"] == {"nameservers": ["1.1.1.1", "8.8.8.8"]}
    for container in containers_of(made, "initContainers") + containers_of(made, "containers"):
        locked = an_object(container["securityContext"])
        assert locked["readOnlyRootFilesystem"] is True
        assert locked["allowPrivilegeEscalation"] is False
        assert locked["runAsNonRoot"] is True
        assert locked["capabilities"] == {"drop": ["ALL"]}
        resources = an_object(container["resources"])
        assert resources["requests"] == resources["limits"]


def test_the_install_fetches_the_release_by_its_digest_and_the_app_runs_it_read_only() -> None:
    made = pod(ENGINE, LAUNCH)
    [install] = containers_of(made, "initContainers")
    [app] = containers_of(made, "containers")
    assert {"name": "PINECALL_SOURCE", "value": SOURCE} in a_list(install["env"])
    assert a_list(install["command"])[:2] == ["timeout", "300"]
    assert app["command"] == [
        "sh",
        "-c",
        '. /run/pinecall/env && exec "$@"',
        "sh",
        "/opt/pinecall/bin/pinecall",
        "start",
        "--prod",
    ]
    mounts = {
        str(an_object(each)["mountPath"]): an_object(each) for each in a_list(app["volumeMounts"])
    }
    assert mounts["/app"]["readOnly"] is True
    assert mounts["/run/pinecall"]["readOnly"] is True
    assert SCRATCH in mounts


def test_a_pod_carries_its_world_its_release_and_its_app_by_a_stamp_a_label_takes() -> None:
    labels = an_object(an_object(pod(ENGINE, LAUNCH)["metadata"])["labels"])
    assert labels == {
        APP_LABEL: app_stamp("org_1/support"),
        WORLD_LABEL: "production",
        RELEASE_LABEL: "1",
    }
    assert "/" not in app_stamp("org_1/support")


def test_the_environment_is_a_file_a_shell_reads_whatever_a_value_holds() -> None:
    secret = environment_secret("support-r1-x", {"B": "it's $HOME", "A": "line\nbreak"})
    assert an_object(secret["metadata"])["name"] == "support-r1-x-env"
    assert an_object(secret["stringData"])["env"] == (
        "export A='line\nbreak'\nexport B='it'\\''s $HOME'\n"
    )
    with pytest.raises(UpstreamFailed, match="not an environment variable"):
        exported({"LD PRELOAD": "x"})


def test_a_pod_still_installing_is_given_the_installs_time_before_it_is_timed() -> None:
    listed: list[Json] = [
        {
            "metadata": {
                "name": "support-r1-x",
                "creationTimestamp": "2026-10-05T10:00:00Z",
                "labels": {RELEASE_LABEL: "1"},
                "annotations": {"pinecall.io/app": "org_1/support"},
            },
            "status": {"phase": "Pending"},
        },
        {
            "metadata": {
                "name": "support-r2-y",
                "creationTimestamp": "2026-10-05T10:00:00Z",
                "labels": {RELEASE_LABEL: "2"},
                "annotations": {"pinecall.io/app": "org_1/support"},
            },
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"name": "app", "state": {"running": {"startedAt": "2026-10-05T10:01:00Z"}}}
                ],
            },
        },
        {"metadata": {"name": "gone"}, "status": {"phase": "Failed"}},
    ]
    pending, running, failed = containers_in(listed)
    assert (pending.app, pending.release, pending.has_exited) == ("org_1/support", 1, False)
    assert running.started_at - pending.started_at == 60.0 - 300
    assert failed.has_exited


async def test_a_verb_knocks_with_the_token_of_the_moment_and_a_refusal_says_why(
    tmp_path: Path,
) -> None:
    token = tmp_path / "token"
    seen: list[str] = []

    def answered(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        if request.method == "DELETE":
            return httpx.Response(404, json={"reason": "NotFound"})
        if request.url.path.endswith("/log"):
            container = request.url.params["container"]
            return httpx.Response(200, text="npm ERR!" if container == "install" else "")
        return httpx.Response(403, text='{"reason":"Forbidden"}')

    async with httpx.AsyncClient(
        base_url="https://cluster", transport=httpx.MockTransport(answered)
    ) as http:
        cluster = Cluster(http=http, engine=ENGINE, token=token)
        token.write_text("first\n")
        await cluster.stop("gone")
        token.write_text("second\n")
        assert await cluster.logs("support-r1-x", 20) == "npm ERR!"
        with pytest.raises(UpstreamFailed, match="the cluster list: 403"):
            await cluster.pods("production")
    assert seen[:2] == ["Bearer first", "Bearer first"]
    assert seen[2:4] == ["Bearer second", "Bearer second"]


async def test_a_host_whose_pod_went_with_its_node_starts_over_its_secret_left_behind(
    tmp_path: Path,
) -> None:
    token = tmp_path / "token"
    token.write_text("t\n")
    seen: list[str] = []

    def answered(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path.rsplit('/', 2)[-2:]}")
        if request.method == "POST" and request.url.path.endswith("/secrets"):
            return httpx.Response(409, json={"reason": "AlreadyExists"})
        return httpx.Response(201, json={})

    async with httpx.AsyncClient(
        base_url="https://cluster", transport=httpx.MockTransport(answered)
    ) as http:
        await Cluster(http=http, engine=ENGINE, token=token).start(LAUNCH, {"A": "1"})
    assert seen == [
        "POST ['pinecall-apps', 'secrets']",
        "PUT ['secrets', 'support-r1-abcdef12-env']",
        "POST ['pinecall-apps', 'pods']",
    ]
