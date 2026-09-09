"""The packages a box runs on, one list: cloud-init installs it at birth, `make install` after."""

import re

import pytest

from tests.tree import ROOT

pytestmark = pytest.mark.unit

CLOUD_INIT = ROOT / "infra" / "box" / "cloud-init.yaml"
MANIFEST = ROOT / "infra" / "box" / "Makefile"


def packages_cloud_init_installs() -> set[str]:
    """The `packages:` block, one `- name` per line, with any comment after the name dropped."""
    block = re.search(r"^packages:\n((?:  - .*\n)+)", CLOUD_INIT.read_text(), re.M)
    assert block is not None, "cloud-init.yaml has no packages: block"
    return {line.strip()[2:].split("#")[0].strip() for line in block.group(1).splitlines()}


def packages_the_manifest_converges() -> set[str]:
    """The PACKAGES line of the manifest, split on whitespace."""
    line = re.search(r"^PACKAGES\s*=\s*(.+)$", MANIFEST.read_text(), re.M)
    assert line is not None, "infra/box/Makefile has no PACKAGES line"
    return set(line.group(1).split())


def test_the_manifest_installs_exactly_what_cloud_init_installs() -> None:
    """A package added to one and not the other is a box that works from birth and never after."""
    assert packages_the_manifest_converges() == packages_cloud_init_installs()


def test_the_speech_tool_a_simulated_caller_speaks_with_is_on_the_list() -> None:
    """Without it the gateway refuses `pinecall simulate --voice` with a 503, on any box."""
    assert "espeak-ng" in packages_cloud_init_installs()
