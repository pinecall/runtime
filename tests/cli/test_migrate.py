"""`pinecall-runtime migrate`: it is registered, it names its verbs, and status touches nothing."""

import pytest

from pinecall.cli import build_parser, main
from pinecall.log.store.migrating import POST_DEPLOY
from pinecall.log.store.postgres import MIGRATIONS

pytestmark = pytest.mark.unit


def test_the_group_is_registered_and_defaults_to_up() -> None:
    arguments = build_parser().parse_args(["migrate"])
    assert (arguments.verb, arguments.schema) == ("up", "public")


def test_an_unknown_verb_is_a_usage_error_today() -> None:
    with pytest.raises(SystemExit) as refused:
        build_parser().parse_args(["migrate", "sideways"])
    assert refused.value.code == 2


def test_plan_lists_what_would_run_and_touches_no_database(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`status` asks the DATABASE; `plan` is the one that answers off the disk alone."""
    assert main(["migrate", "plan"]) == 0

    printed = capsys.readouterr().out.split()
    startup = sorted(p.name for p in MIGRATIONS.glob("*.sql") if not p.name.endswith(POST_DEPLOY))
    assert printed == startup
    assert printed, "the distribution ships at least the table"


def test_plan_post_lists_the_ones_a_deploy_does_not_wait_for(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """They are named and never run at startup, so `--post` is how a person sees them at all."""
    assert main(["migrate", "plan", "--post"]) == 0

    printed = capsys.readouterr().out.split()
    assert all(name.endswith(POST_DEPLOY) for name in printed)
