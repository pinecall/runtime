"""`pinecall-runtime memory` is a group of the CLI with one verb, reembed."""

from __future__ import annotations

import pytest

from pinecall.cli import GROUPS, build_parser
from pinecall.cli.memory.verbs import run

pytestmark = pytest.mark.unit


def test_memory_is_a_group_and_reembed_its_verb() -> None:
    assert "memory" in GROUPS
    arguments = build_parser().parse_args(["memory", "reembed"])
    assert arguments.verb == "reembed"
    assert arguments.run is run


def test_no_other_verb_is_taken() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["memory", "forget"])
