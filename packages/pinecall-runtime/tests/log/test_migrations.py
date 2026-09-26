"""The migrations as a set: numbered, applied in name order, and the only place DDL is written."""

import re

import pytest

from pinecall.log.store.migrating import every, file_hash
from pinecall.log.store.postgres import MIGRATIONS
from tests.tree import PACKAGE_ROOT, ROOT, modules_under

pytestmark = pytest.mark.unit

# `create table`, `alter table`, `drop table`, in any case: what a schema is changed with.
DDL = re.compile(r"\b(create|alter|drop)\s+table\b", re.IGNORECASE)

# The one exception, named here so nobody has to guess whether it was an accident: the runner's own
# bookkeeping table cannot be a migration, because it is what records that a migration ran.
THE_RUNNERS_OWN_TABLE = (PACKAGE_ROOT / "log" / "store" / "migrating.py").relative_to(ROOT)


def test_every_migration_is_numbered_so_the_order_they_apply_in_is_the_order_they_read_in() -> None:
    """A migration is added, never edited, and name order is the only order there is."""
    names = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    # A post-deployment file says so in its name (log/store/migrating.py, POST_DEPLOY), and is
    # numbered in the same sequence as the rest.
    assert names == [name for name in names if re.match(r"^\d{4}_[a-z_]+(\.post)?\.sql$", name)]
    assert [name[:4] for name in names] == [f"{n:04d}" for n in range(1, len(names) + 1)]


def the_lock() -> str:
    """The name `migrations.lock` holds: the last migration that landed."""
    named = [
        line.strip()
        for line in (MIGRATIONS / "migrations.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(named) == 1, f"migrations.lock names one migration, not {named}"
    return named[0]


def test_every_landed_migration_is_the_file_the_databases_that_ran_it_ran() -> None:
    """A landed migration edited in a pull request fails here, not at a box's next startup.

    The databases keep each file's sha256 and refuse a gateway whose file changed
    (log/store/migrating.py); `applied.sha256` is that record kept in the tree, for what is at or
    below the lock — above it nothing has run anywhere, and the file is still the author's to
    edit. The rename of 2026-09-26 edited a comment in nine landed files, CI said green, and
    production did not start.
    """
    kept: dict[str, str] = {}
    for line in (MIGRATIONS / "applied.sha256").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            sha, name = line.split(maxsplit=1)
            kept[name] = sha
    landed = {path.name: file_hash(path) for path in every() if path.name <= the_lock()}
    not_landed = sorted(kept.keys() - landed.keys())
    assert not not_landed, (
        f"{not_landed} has not landed: its line goes in with the bump of migrations.lock"
    )
    missing = sorted(landed.keys() - kept.keys())
    assert not missing, f"append the landed migration's line to applied.sha256: {missing}"
    edited = sorted(name for name, sha in landed.items() if kept[name] != sha)
    assert not edited, (
        f"{edited} changed after it ran: every database that ran it has the old one. Restore the "
        "file and put the change in a new migration"
    )


def test_the_routes_and_tokens_tables_are_created_by_a_migration_and_nowhere_else() -> None:
    """DDL lives in a .sql file, never in a string constant with a promise attached to it."""
    said = "\n".join(path.read_text(encoding="utf-8") for path in MIGRATIONS.glob("*.sql"))
    assert DDL.search(said) is not None
    assert "create table if not exists routes" in said.lower()
    assert "create table if not exists tokens" in said.lower()


def test_no_python_module_but_the_migration_runner_writes_ddl() -> None:
    """The runner bootstraps its own record of what it applied; every other table is a migration."""
    offenders = [
        str(module.path)
        for module in modules_under(PACKAGE_ROOT)
        if module.path != THE_RUNNERS_OWN_TABLE
        and DDL.search((ROOT / module.path).read_text("utf-8"))
    ]
    assert not offenders, f"DDL outside pinecall/migrations/: {offenders}"
