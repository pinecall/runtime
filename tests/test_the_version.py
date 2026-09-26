"""One version for the workspace: every distribution says it, and every sibling is pinned to it."""

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from pinecall._version import __version__
from pinecall_testkit.tree import DISTRIBUTIONS

pytestmark = pytest.mark.unit

# A requirement as a pyproject writes one: its name, its extras, and what it asks of the version.
A_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[^\]]*\])?(?P<spec>.*)$")

# The protocol is the one sibling released apart, in its own repository: it travels with a range.
THE_PROTOCOL = "pinecall-protocol"


def project_of(distribution: Path) -> dict[str, Any]:
    """The [project] table of a distribution's pyproject."""
    table = tomllib.loads((distribution / "pyproject.toml").read_text(encoding="utf-8"))
    project: dict[str, Any] = table["project"]
    return project


def requirements_of(distribution: Path) -> list[str]:
    """Every requirement a distribution declares, its extras' included."""
    project = project_of(distribution)
    extras: dict[str, list[str]] = project.get("optional-dependencies", {})
    return [*project.get("dependencies", []), *(r for group in extras.values() for r in group)]


# The application's version is dynamic, read from _version.py by hatch; every other one writes it.
@pytest.mark.parametrize("distribution", DISTRIBUTIONS, ids=lambda d: d.name)
def test_every_distribution_is_at_the_one_version(distribution: Path) -> None:
    project = project_of(distribution)
    if "version" not in project.get("dynamic", []):
        assert project["version"] == __version__, f"{distribution.name} is not at {__version__}"


# A sibling is pinned exactly: the distributions are one product released together, so
# `pip install pinecall==X` pulls the eleven that were built beside it and no others.
@pytest.mark.parametrize("distribution", DISTRIBUTIONS, ids=lambda d: d.name)
def test_every_sibling_is_pinned_to_the_one_version(distribution: Path) -> None:
    loose = []
    for requirement in requirements_of(distribution):
        parsed = A_REQUIREMENT.match(requirement)
        assert parsed is not None, f"{distribution.name}: {requirement!r} does not parse"
        name, spec = parsed["name"], parsed["spec"].strip()
        is_a_sibling = name == "pinecall" or name.startswith("pinecall-")
        if is_a_sibling and name != THE_PROTOCOL and spec != f"=={__version__}":
            loose.append(requirement)
    assert not loose, f"{distribution.name} pins a sibling loosely: {loose}"


# In the tree the protocol is a path ([tool.uv.sources]) that a wheel does not carry: published
# with a bare name, a version of ours would accept whatever protocol tomorrow brings.
@pytest.mark.parametrize("distribution", DISTRIBUTIONS, ids=lambda d: d.name)
def test_the_protocol_travels_with_a_range(distribution: Path) -> None:
    bare = [
        requirement
        for requirement in requirements_of(distribution)
        if (parsed := A_REQUIREMENT.match(requirement)) is not None
        and parsed["name"] == THE_PROTOCOL
        and not parsed["spec"].strip()
    ]
    assert not bare, f"{distribution.name} names the protocol with no range: {bare}"
