#!/usr/bin/env python3
"""A fleet loop's cloud on AWS: the machines of an Auto Scaling group, made and let go."""

# `fleet loop --cloud infra/fleet/aws-asg.py …` runs it with one verb:
#   create <name>            one machine more: the group's desired capacity raised by one. EC2
#                            names a machine by its instance id, which is its hostname and so its
#                            worker's name (modules/fleet-aws); the name asked for is not kept
#   delete <name>            the machine terminated out of the group, its desired capacity one
#                            less: the loop does it once the worker drained (the group would end
#                            a machine it removes itself, call or no call)
#   list                     the group's machines: name<TAB>created (ISO 8601)
# The group has no scaling policy: the loop is the one thing that changes its size.
# Set: PINECALL_FLEET_ASG; the region and the credentials are the aws CLI's (AWS_REGION, the
# machine's instance profile or the operator's ~/.aws). Python's standard library and the aws CLI.

import json
import os
import subprocess
import sys
from typing import Any


def main(argv: list[str]) -> int:
    """Run the verb the loop asked for."""
    verb, *rest = argv or ["help"]
    if verb == "list":
        for name, made_at in machines():
            print(f"{name}\t{made_at}")
        return 0
    if verb == "create" and len(rest) == 1:
        created()
        return 0
    if verb == "delete" and len(rest) == 1:
        deleted(rest[0])
        return 0
    print("aws-asg.py create <name> | delete <name> | list", file=sys.stderr)
    return 2


def machines() -> list[tuple[str, str]]:
    """Each machine of the group still in it, by instance id, with when it was launched."""
    ids = [
        instance["InstanceId"]
        for instance in _group()["Instances"]
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


def created() -> None:
    """One machine more: the group's desired capacity raised by one."""
    _aws(
        "autoscaling",
        "set-desired-capacity",
        "--auto-scaling-group-name",
        _setting("PINECALL_FLEET_ASG"),
        "--desired-capacity",
        str(_group()["DesiredCapacity"] + 1),
    )


def _group() -> dict[str, Any]:
    groups = _aws(
        "autoscaling",
        "describe-auto-scaling-groups",
        "--auto-scaling-group-names",
        _setting("PINECALL_FLEET_ASG"),
    )
    return groups["AutoScalingGroups"][0]


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
