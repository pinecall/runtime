#!/usr/bin/env python3
"""A fleet loop's cloud on AWS when an Auto Scaling group grows the fleet itself."""

# `fleet loop --cloud infra/fleet/aws-asg.py --grow-at-most 0 …` runs it with one verb:
#   list                     the group's machines: name<TAB>created (ISO 8601); a machine's name is
#                            its instance id, which is its hostname (modules/fleet-aws)
#   delete <name>            the machine terminated out of the group, its desired capacity one
#                            less: the loop does it once the worker drained (the group would end
#                            a machine it removes itself, call or no call)
#   measure <fleet> <calls>  the fleet's calls written to CloudWatch as Pinecall/fleet_calls,
#                            which the group's target tracking grows on
#   create <name>            refused: the group grows
# Set: PINECALL_FLEET_ASG; the region and the credentials are the aws CLI's (AWS_REGION, the
# machine's instance profile or the operator's ~/.aws). Python's standard library and the aws CLI.

import json
import os
import subprocess
import sys
from typing import Any

MEASURE_TAKES = 2
NAMESPACE = "Pinecall"
METRIC = "fleet_calls"


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
        print("the Auto Scaling group grows the fleet; the loop only lets go", file=sys.stderr)
        return 2
    print("aws-asg.py list | delete <name> | measure <fleet> <calls>", file=sys.stderr)
    return 2


def machines() -> list[tuple[str, str]]:
    """Each machine of the group still in it, by instance id, with when it was launched."""
    groups = _aws(
        "autoscaling",
        "describe-auto-scaling-groups",
        "--auto-scaling-group-names",
        _setting("PINECALL_FLEET_ASG"),
    )
    ids = [
        instance["InstanceId"]
        for group in groups["AutoScalingGroups"]
        for instance in group["Instances"]
        if instance["LifecycleState"] in ("Pending", "InService")
    ]
    if not ids:
        return []
    described = _aws("ec2", "describe-instances", "--instance-ids", *ids)
    return [
        (instance["InstanceId"], instance["LaunchTime"])
        for reservation in described["Reservations"]
        for instance in reservation["Instances"]
    ]


def deleted(name: str) -> None:
    """The machine terminated out of the group; one gone already is left as it is."""
    ids = {item[0] for item in machines()}
    if name not in ids:
        return
    _aws(
        "autoscaling",
        "terminate-instance-in-auto-scaling-group",
        "--instance-id",
        name,
        "--should-decrement-desired-capacity",
    )


def measured(fleet: str, calls: int) -> None:
    """One point of the fleet's calls, now."""
    _aws(
        "cloudwatch",
        "put-metric-data",
        "--namespace",
        NAMESPACE,
        "--metric-name",
        METRIC,
        "--dimensions",
        f"fleet={fleet}",
        "--value",
        str(calls),
        "--unit",
        "Count",
    )


def _setting(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"aws-asg.py: {name} is not set")
    return value


def _aws(*argv: str) -> dict[str, Any]:
    done = subprocess.run(
        ("aws", *argv, "--output", "json"), capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        sys.exit(f"aws-asg.py: aws {argv[0]} {argv[1]}: {done.stderr.strip()[-400:]}")
    return json.loads(done.stdout) if done.stdout.strip() else {}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
