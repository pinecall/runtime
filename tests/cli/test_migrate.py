"""`pinecall-runtime migrate`: it is registered, it names its verbs, and status touches nothing."""

import pytest

from pinecall.cli import build_parser, main
from pinecall.log.store.postgres import MIGRATIONS

pytestmark = pytest.mark.unit


def test_the_group_is_registered_and_defaults_to_up() -> None:
    arguments = build_parser().parse_args(["migrate"])
    assert (arguments.verb, arguments.schema) == ("up", "public")


def test_an_unknown_verb_is_a_usage_error_today() -> None:
    with pytest.raises(SystemExit) as refused:
        build_parser().parse_args(["migrate", "sideways"])
    assert refused.value.code == 2


def test_status_lists_the_files_the_distribution_ships(capsys: pytest.CaptureFixture[str]) -> None:
    """No database is touched: a person can read what would run before running it."""
    assert main(["migrate", "status"]) == 0
    printed = capsys.readouterr().out.split()
    assert printed == sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    assert printed, "the distribution ships at least the table"
