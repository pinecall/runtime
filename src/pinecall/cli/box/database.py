"""`pinecall-runtime box database`: an instance's own database on the box's Postgres, made once."""

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from typing import TextIO
from urllib.parse import unquote, urlsplit

from pinecall._exceptions import PinecallError
from pinecall._settings import load_settings

# The box runs one Postgres, and every instance's database lives in it; this is the one account
# that may make another — the container's own superuser, reached as root through podman, so no
# password of it is ever read here. The SQL travels on stdin: an instance's password is in it.
PSQL = (
    "podman",
    "exec",
    "-i",
    "pinecall-postgres",
    "psql",
    "-U",
    "pinecall",
    "-v",
    "ON_ERROR_STOP=1",
    "-qtA",
)
MAINTENANCE = "postgres"

# What an identifier of ours looks like: `pinecall`, `pinecall_sandbox`. Anything else in a DSN is
# a hand-written one, and a name is spliced into SQL here, so it is refused rather than quoted.
AN_IDENTIFIER = r"^[a-z_][a-z0-9_]{0,62}$"

THERE = "{database} is there: nothing to make"
MADE = "made {database}, owned by {role}, which may connect to no other database"
REFUSED = "postgres refused in {database}: {said}"
NOT_OURS = "DATABASE_URL names {what} {name}: an instance's database and role are lowercase words"

# Isolation is CONNECT, and it has to be taken from PUBLIC: every role holds CONNECT on every
# database through PUBLIC, so revoking it from the new role alone would change nothing. The owners
# keep theirs — an owner's rights are its own — and so does the superuser production runs as.
REVOKE_FROM_EVERYONE = """DO $$
DECLARE database text;
BEGIN
  FOR database IN SELECT datname FROM pg_database WHERE NOT datistemplate LOOP
    EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', database);
  END LOOP;
END $$;"""
# Both halves of hybrid search, as infra/compose/postgres/init.sql makes them in the first database
# on first boot — which is the only database that script ever sees.
EXTENSIONS = "CREATE EXTENSION IF NOT EXISTS vector;\nCREATE EXTENSION IF NOT EXISTS pg_textsearch;"


class NotOurDatabase(PinecallError):
    """DATABASE_URL names a database or role this verb will not splice into SQL."""


class PostgresRefused(PinecallError):
    """psql in the box's Postgres said no; its ERROR line, never the statement it stopped at."""


type Psql = Callable[[str, str], str]
"""psql in a database, the SQL on stdin, its output back."""


def configure(parser: argparse.ArgumentParser) -> None:
    """`box database`: run by pinecall-db@<name> as root, with that instance's DATABASE_URL."""
    parser.set_defaults(run=run_database)


def run_database(_arguments: argparse.Namespace) -> int:
    """The DATABASE_URL this process was handed is the database to make."""
    return make_database(load_settings().database_url)


def make_database(dsn: str, psql: Psql | None = None, out: TextIO = sys.stdout) -> int:
    """Nothing where the database is there, as production's always is; else its role, it, walls."""
    asking = psql or psql_in_the_container
    parts = urlsplit(dsn)
    database, role = _ours("database", parts.path.lstrip("/")), _ours("role", parts.username)
    if asking(MAINTENANCE, f"SELECT 1 FROM pg_database WHERE datname = '{database}';").strip():
        print(THERE.format(database=database), file=out)
        return 0
    password = unquote(parts.password or "").replace("'", "''")
    asking(
        MAINTENANCE,
        f"""DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
    CREATE ROLE {role} LOGIN PASSWORD '{password}';
  END IF;
END $$;
CREATE DATABASE {database} OWNER {role};
{REVOKE_FROM_EVERYONE}""",
    )
    asking(database, EXTENSIONS)
    print(MADE.format(database=database, role=role), file=out)
    return 0


# Only psql's ERROR lines are said: the LINE excerpt under one quotes the statement it stopped
# at, and one of these statements carries the new role's password into a journal.
def psql_in_the_container(database: str, sql: str) -> str:
    """`podman exec -i pinecall-postgres psql -U pinecall -d <database>`, the SQL on stdin."""
    done = subprocess.run(  # noqa: S603 — every argument is ours; the SQL, with a password, is stdin
        [*PSQL, "-d", database], input=sql, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        said = [line for line in done.stderr.splitlines() if line.startswith(("ERROR", "psql:"))]
        raise PostgresRefused(
            REFUSED.format(database=database, said=" ".join(said[:1]) or done.returncode)
        )
    return done.stdout


def _ours(what: str, name: str | None) -> str:
    """An identifier this verb may write into SQL unquoted, or the refusal naming it."""
    if name is None or not re.match(AN_IDENTIFIER, name):
        raise NotOurDatabase(NOT_OURS.format(what=what, name=name))
    return name
