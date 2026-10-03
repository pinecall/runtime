"""Tests for infra/fleet/gcp-mig.py: the group's machines listed, made and let go, on a fake API."""

import io
import json
import runpy
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Self

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "infra" / "fleet" / "gcp-mig.py"

AT = "projects/lab/zones/us-central1-c"

GROUP = f"{AT}/instanceGroupManagers/pinecall-workers-production"

SETTINGS = {
    "PINECALL_FLEET_PROJECT": "lab",
    "PINECALL_FLEET_ZONE": "us-central1-c",
    "PINECALL_FLEET_MIG": "pinecall-workers-production",
}


class Answered:
    """What urlopen gives back: a body read once, usable as a context."""

    def __init__(self, body: object) -> None:
        """The body, as JSON."""
        self.body = json.dumps(body).encode()

    def read(self) -> bytes:
        """The body."""
        return self.body

    def __enter__(self) -> Self:
        """Itself."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Nothing to close."""


def refusal(code: int, body: bytes) -> urllib.error.HTTPError:
    """The API's refusal, with its body."""
    return urllib.error.HTTPError(f"https://x/{code}", code, "refused", Message(), io.BytesIO(body))


class ComputeApi:
    """A Compute API that answers from a table and remembers every request it was sent."""

    def __init__(self, answers: dict[str, object]) -> None:
        """The answers by URL path after /v1/, and nothing sent yet."""
        self.answers = answers
        self.sent: list[tuple[str, str, object]] = []

    def opened(self, request: urllib.request.Request, **_timeout: float) -> Answered:
        """One request: a token from the metadata server, else the table's answer or a 404."""
        url = request.full_url
        if "metadata.google.internal" in url:
            return Answered({"access_token": "t"})
        where = url.rsplit("/v1/", maxsplit=1)[-1]
        data = request.data
        self.sent.append(
            (request.get_method(), where, json.loads(data) if isinstance(data, bytes) else None)
        )
        answer = self.answers.get(where, refusal(404, b"no such"))
        if isinstance(answer, urllib.error.HTTPError):
            raise answer
        return Answered(answer)


def ran(monkeypatch: pytest.MonkeyPatch, api: ComputeApi, *argv: str) -> tuple[int, list[str]]:
    """The script's main, run on the fake API: its exit code and the lines it printed."""
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(urllib.request, "urlopen", api.opened)
    printed: list[str] = []

    def kept(*parts: object, **_given: object) -> None:
        printed.append(" ".join(str(part) for part in parts))

    monkeypatch.setattr("builtins.print", kept)
    code: int = runpy.run_path(str(SCRIPT))["main"](list(argv))
    return code, printed


# The group lists a machine it is deleting until it is gone: the loop would delete it every tick.
def test_list_names_the_machines_and_not_one_the_group_is_deleting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listed = {
        "managedInstances": [
            {"instance": f"https://x/{AT}/instances/pinecall-worker-1", "currentAction": "NONE"},
            {
                "instance": f"https://x/{AT}/instances/pinecall-worker-2",
                "currentAction": "DELETING",
            },
        ]
    }
    made = {"creationTimestamp": "2026-10-03T15:23:11.000-07:00"}
    api = ComputeApi(
        {f"{GROUP}/listManagedInstances": listed, f"{AT}/instances/pinecall-worker-1": made}
    )
    assert ran(monkeypatch, api, "list") == (
        0,
        ["pinecall-worker-1\t2026-10-03T15:23:11.000-07:00"],
    )


def test_create_asks_the_group_for_the_name_and_waits_for_the_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ComputeApi(
        {
            f"{GROUP}/createInstances": {"name": "op-1", "status": "RUNNING"},
            f"{AT}/operations/op-1/wait": {"name": "op-1", "status": "DONE"},
        }
    )
    assert ran(monkeypatch, api, "create", "pinecall-worker-9")[0] == 0
    assert api.sent == [
        ("POST", f"{GROUP}/createInstances", {"instances": [{"name": "pinecall-worker-9"}]}),
        ("POST", f"{AT}/operations/op-1/wait", {}),
    ]


def test_delete_of_a_machine_already_out_of_the_group_is_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ComputeApi({f"{GROUP}/deleteInstances": refusal(400, b"instance is not a member")})
    assert ran(monkeypatch, api, "delete", "pinecall-worker-9")[0] == 0
    assert [sent[1] for sent in api.sent] == [f"{GROUP}/deleteInstances"]


def test_a_verb_it_does_not_have_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ran(monkeypatch, ComputeApi({}), "measure", "pinecall", "4")[0] == 2
