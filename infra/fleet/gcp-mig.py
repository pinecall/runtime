#!/usr/bin/env python3
"""A fleet loop's cloud on Google Cloud when a managed instance group grows the fleet itself."""

# `fleet loop --cloud infra/fleet/gcp-mig.py --grow-at-most 0 …` runs it with one verb:
#   list                     the group's machines: name<TAB>created (ISO 8601)
#   delete <name>            the machine abandoned from the group, which no longer counts it, then
#                            deleted: the loop does it once the worker drained (a group gives a
#                            machine it removes itself 90 s, a call may take ten minutes)
#   measure <fleet> <calls>  the fleet's calls written to Cloud Monitoring as
#                            custom.googleapis.com/pinecall/fleet_calls, which the group's
#                            autoscaler grows on (modules/fleet-gcp)
#   create <name>            refused: the group grows
# Set: PINECALL_FLEET_PROJECT (the metadata server's project unless set), PINECALL_FLEET_ZONE,
# PINECALL_FLEET_MIG. The token is the machine's own (its service account, from the metadata
# server) on Google Cloud, else the gcloud login's. Python's standard library alone.

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

METADATA = "http://metadata.google.internal/computeMetadata/v1"
COMPUTE = "https://compute.googleapis.com/compute/v1"
MONITORING = "https://monitoring.googleapis.com/v3"
METRIC = "custom.googleapis.com/pinecall/fleet_calls"
MEASURE_TAKES = 2
NOT_FOUND = 404
BAD_REQUEST = 400


def main(argv: list[str]) -> int:
    """Run the verb the loop asked for."""
    verb, *rest = argv or ["help"]
    if verb == "list":
        for name, created in machines():
            print(f"{name}\t{created}")
        return 0
    if verb == "delete" and len(rest) == 1:
        deleted(rest[0])
        return 0
    if verb == "measure" and len(rest) == MEASURE_TAKES:
        measured(rest[0], int(rest[1]))
        return 0
    if verb == "create":
        print("the managed instance group grows the fleet; the loop only lets go", file=sys.stderr)
        return 2
    print("gcp-mig.py list | delete <name> | measure <fleet> <calls>", file=sys.stderr)
    return 2


def machines() -> list[tuple[str, str]]:
    """Each machine of the group, by name, with when it was made."""
    group = _group()
    answer = _call("POST", f"{group}/listManagedInstances", {})
    names = [item["instance"].rsplit("/", 1)[1] for item in answer.get("managedInstances", [])]
    found: list[tuple[str, str]] = []
    for name in names:
        instance = _call("GET", f"{_zone()}/instances/{name}", None, missing_ok=True)
        if instance:
            found.append((name, instance["creationTimestamp"]))
    return found


def deleted(name: str) -> None:
    """The machine out of the group, then gone; one already out, or gone, is left as it is."""
    instance = f"zones/{_setting('PINECALL_FLEET_ZONE')}/instances/{name}"
    _call("POST", f"{_group()}/abandonInstances", {"instances": [instance]}, missing_ok=True)
    _call("DELETE", f"{_zone()}/instances/{name}", None, missing_ok=True)


def measured(fleet: str, calls: int) -> None:
    """One point of the fleet's calls, now."""
    point = {
        "interval": {"endTime": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "value": {"int64Value": str(calls)},
    }
    series = {
        "metric": {"type": METRIC, "labels": {"fleet": fleet}},
        "resource": {"type": "global", "labels": {"project_id": _project()}},
        "points": [point],
    }
    _call("POST", f"{MONITORING}/projects/{_project()}/timeSeries", {"timeSeries": [series]})


def _group() -> str:
    return f"{_zone()}/instanceGroupManagers/{_setting('PINECALL_FLEET_MIG')}"


def _zone() -> str:
    return f"{COMPUTE}/projects/{_project()}/zones/{_setting('PINECALL_FLEET_ZONE')}"


def _project() -> str:
    return os.environ.get("PINECALL_FLEET_PROJECT") or _metadata("project/project-id")


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
        with urllib.request.urlopen(asked, timeout=30) as answer:
            text = answer.read().decode()
    except urllib.error.HTTPError as refused:
        gone = refused.code == NOT_FOUND or (
            refused.code == BAD_REQUEST and "not a member" in str(refused.reason)
        )
        if missing_ok and gone:
            return {}
        detail = refused.read().decode()[:400]
        where = url.rsplit("/v1/", maxsplit=1)[-1]
        sys.exit(f"gcp-mig.py: {method} {where} answered {refused.code}: {detail}")
    return json.loads(text) if text else {}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
