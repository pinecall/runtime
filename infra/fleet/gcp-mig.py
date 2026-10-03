#!/usr/bin/env python3
"""A fleet loop's cloud on Google Cloud: a managed instance group's machines, made and let go."""

# `fleet loop --cloud infra/fleet/gcp-mig.py …` runs it with one verb:
#   create <name>            a machine of that name made by the group from its template (the image
#                            family, the world's identity, no public address), the call returning
#                            once the machine exists; it boots, reads its credentials from Secret
#                            Manager and registers
#   delete <name>            the machine deleted out of the group, the call returning once it is
#                            gone: the loop does it once the worker drained (a group that removed
#                            a machine itself would give it 90 s, and a call may take ten minutes)
#   list                     the group's machines: name<TAB>created (ISO 8601)
# The group has no autoscaler: the loop is the one thing that changes its size (modules/fleet-gcp).
# Set: PINECALL_FLEET_PROJECT (the metadata server's project unless set), PINECALL_FLEET_ZONE,
# PINECALL_FLEET_MIG. The token is the machine's own (its service account, from the metadata
# server) on Google Cloud, else the gcloud login's. Python's standard library alone.

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

METADATA = "http://metadata.google.internal/computeMetadata/v1"
COMPUTE = "https://compute.googleapis.com/compute/v1"
NOT_FOUND = 404
BAD_REQUEST = 400
# An operation's wait answers within two minutes, done or not; making a machine takes under one.
WAITS = 3


def main(argv: list[str]) -> int:
    """Run the verb the loop asked for."""
    verb, *rest = argv or ["help"]
    if verb == "list":
        for name, made_at in machines():
            print(f"{name}\t{made_at}")
        return 0
    if verb == "create" and len(rest) == 1:
        created(rest[0])
        return 0
    if verb == "delete" and len(rest) == 1:
        deleted(rest[0])
        return 0
    print("gcp-mig.py create <name> | delete <name> | list", file=sys.stderr)
    return 2


# A machine the group is deleting stays listed until it is gone, about a minute after the delete
# returned: it is no longer the fleet's, or the loop would decide its delete again every tick.
def machines() -> list[tuple[str, str]]:
    """Each machine of the group that is not on its way out, by name, with when it was made."""
    group = _group()
    answer = _call("POST", f"{group}/listManagedInstances", {})
    names = [
        item["instance"].rsplit("/", 1)[1]
        for item in answer.get("managedInstances", [])
        if item.get("currentAction") != "DELETING"
    ]
    found: list[tuple[str, str]] = []
    for name in names:
        instance = _call("GET", f"{_zone()}/instances/{name}", None, missing_ok=True)
        if instance:
            found.append((name, instance["creationTimestamp"]))
    return found


def created(name: str) -> None:
    """A machine of this name made by the group; back once it exists."""
    _done(_call("POST", f"{_group()}/createInstances", {"instances": [{"name": name}]}))


def deleted(name: str) -> None:
    """The machine deleted out of the group, back once it is gone; one gone already is left."""
    instance = f"zones/{_setting('PINECALL_FLEET_ZONE')}/instances/{name}"
    begun = _call("POST", f"{_group()}/deleteInstances", {"instances": [instance]}, missing_ok=True)
    if begun:
        _done(begun)


def _done(operation: dict[str, Any]) -> None:
    for _ in range(WAITS):
        if operation.get("status") == "DONE":
            break
        operation = _call("POST", f"{_zone()}/operations/{operation['name']}/wait", {})
    failed = operation.get("error", {}).get("errors", [])
    if failed:
        sys.exit(f"gcp-mig.py: {operation.get('operationType')}: {failed[0].get('message')}")
    if operation.get("status") != "DONE":
        sys.exit(f"gcp-mig.py: {operation.get('operationType')} is still running")


def _group() -> str:
    return f"{_zone()}/instanceGroupManagers/{_setting('PINECALL_FLEET_MIG')}"


def _zone() -> str:
    return f"{COMPUTE}/projects/{_project()}/zones/{_setting('PINECALL_FLEET_ZONE')}"


def _project() -> str:
    if os.environ.get("PINECALL_FLEET_PROJECT"):
        return os.environ["PINECALL_FLEET_PROJECT"]
    try:
        return _metadata("project/project-id")
    except (urllib.error.URLError, OSError):
        sys.exit("gcp-mig.py: PINECALL_FLEET_PROJECT is not set, and no metadata server answers")


def _setting(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"gcp-mig.py: {name} is not set")
    return value


def _token() -> str:
    try:
        answer = _metadata("instance/service-accounts/default/token")
        return json.loads(answer)["access_token"]
    except (urllib.error.URLError, OSError):
        login = ("gcloud", "auth", "print-access-token")
        return subprocess.run(login, capture_output=True, text=True, check=True).stdout.strip()


def _metadata(path: str) -> str:
    asked = urllib.request.Request(f"{METADATA}/{path}", headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(asked, timeout=2) as answer:
        return answer.read().decode()


def _call(method: str, url: str, body: object, *, missing_ok: bool = False) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}
    asked = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(asked, timeout=150) as answer:
            text = answer.read().decode()
    except urllib.error.HTTPError as refused:
        detail = refused.read().decode()
        gone = refused.code == NOT_FOUND or (
            refused.code == BAD_REQUEST and "not a member" in detail
        )
        if missing_ok and gone:
            return {}
        where = url.rsplit("/v1/", maxsplit=1)[-1]
        sys.exit(f"gcp-mig.py: {method} {where} answered {refused.code}: {detail[:400]}")
    return json.loads(text) if text else {}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
