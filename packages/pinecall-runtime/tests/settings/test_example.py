""".env.example is generated: this pins it to the aliases Settings declares, name for name."""

from pathlib import Path

import pytest

from pinecall.settings import Settings, variable_of
from pinecall.settings.example import render_env_example
from tests.tree import ROOT

pytestmark = pytest.mark.unit

THE_EXAMPLE = ROOT / ".env.example"


def test_the_committed_example_is_what_the_generator_renders() -> None:
    """`scripts/generate-env-example` is the only way this file changes."""
    assert _example_on_disk() == render_env_example(), "run scripts/generate-env-example"


def test_the_example_names_exactly_the_aliases_the_settings_declare() -> None:
    declared = [variable_of(name) for name in Settings.model_fields]
    assert _names_in(_example_on_disk()) == declared


def test_the_example_carries_one_comment_line_for_every_name() -> None:
    lines = [line for line in _example_on_disk().splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if "=" in line and not line.startswith("#"):
            assert lines[index - 1].startswith("# "), f"{line.split('=')[0]} has no comment"


def _example_on_disk() -> str:
    return Path(THE_EXAMPLE).read_text(encoding="utf-8")


def _names_in(example: str) -> list[str]:
    return [
        line.split("=", 1)[0] for line in example.splitlines() if line and not line.startswith("#")
    ]


def test_the_world_is_written_as_production_which_is_what_a_box_that_says_nothing_is() -> None:
    assert "\nPINECALL_WORLD=production\n" in _example_on_disk()
