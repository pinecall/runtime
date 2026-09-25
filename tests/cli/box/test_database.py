"""`box database`: an instance's database made once, walled off, and production's never touched."""

from io import StringIO

import pytest

from pinecall.cli.box.database import (
    EXTENSIONS,
    MADE,
    MAINTENANCE,
    NOT_OURS,
    THERE,
    NotOurDatabase,
    make_database,
)

pytestmark = pytest.mark.unit

SANDBOX = "postgresql://pinecall_sandbox:s3cret@127.0.0.1:5432/pinecall_sandbox"
PRODUCTION = "postgresql://pinecall:p4ss@127.0.0.1:5432/pinecall"


class Postgres:
    """psql stood in for: which databases exist, and every (database, sql) it was handed."""

    def __init__(self, *existing: str) -> None:
        self.existing = set(existing)
        self.asked: list[tuple[str, str]] = []

    def __call__(self, database: str, sql: str) -> str:
        self.asked.append((database, sql))
        return "1\n" if any(f"datname = '{name}'" in sql for name in self.existing) else ""


def test_production_whose_database_the_container_made_is_left_exactly_as_it_is() -> None:
    postgres = Postgres("pinecall")
    said = StringIO()
    assert make_database(PRODUCTION, postgres, said) == 0
    assert len(postgres.asked) == 1
    assert said.getvalue() == THERE.format(database="pinecall") + "\n"


def test_a_missing_database_gets_its_role_itself_the_walls_and_both_extensions() -> None:
    postgres = Postgres("pinecall")
    said = StringIO()
    assert make_database(SANDBOX, postgres, said) == 0
    (_, looked), (where, made), (inside, extended) = postgres.asked
    assert "pinecall_sandbox" in looked
    assert where == MAINTENANCE
    assert "CREATE ROLE pinecall_sandbox LOGIN PASSWORD 's3cret'" in made
    assert "CREATE DATABASE pinecall_sandbox OWNER pinecall_sandbox" in made
    # From PUBLIC, on every database: revoking from the role alone leaves PUBLIC's CONNECT to it.
    assert "REVOKE CONNECT ON DATABASE %I FROM PUBLIC" in made
    assert (inside, extended) == ("pinecall_sandbox", EXTENSIONS)
    assert (
        said.getvalue() == MADE.format(database="pinecall_sandbox", role="pinecall_sandbox") + "\n"
    )
    assert "s3cret" not in said.getvalue()


def test_a_role_left_by_a_run_that_died_halfway_is_not_made_twice() -> None:
    postgres = Postgres()
    make_database(SANDBOX, postgres, StringIO())
    assert (
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pinecall_sandbox')"
        in (postgres.asked[1][1])
    )


@pytest.mark.parametrize(
    ("dsn", "what", "name"),
    [
        ("postgresql://pinecall:x@127.0.0.1:5432/pinecall;drop", "database", "pinecall;drop"),
        ("postgresql://Robert:x@127.0.0.1:5432/pinecall_a", "role", "Robert"),
        ("postgresql://127.0.0.1:5432/pinecall_a", "role", None),
    ],
)
def test_a_name_that_is_not_an_identifier_of_ours_is_never_spliced_into_sql(
    dsn: str, what: str, name: str | None
) -> None:
    postgres = Postgres()
    with pytest.raises(NotOurDatabase) as refused:
        make_database(dsn, postgres, StringIO())
    assert str(refused.value) == NOT_OURS.format(what=what, name=name)
    assert postgres.asked == []
