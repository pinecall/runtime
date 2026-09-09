"""The migrations as a set: numbered, applied in name order, and the only place DDL is written."""

import re

import pytest

from pinecall.log.store.postgres import MIGRATIONS
from tests.tree import PACKAGE_ROOT, ROOT, modules_under

pytestmark = pytest.mark.unit

# `create table`, `alter table`, `drop table`, in any case: what a schema is changed with.
DDL = re.compile(r"\b(create|alter|drop)\s+table\b", re.IGNORECASE)

# The one exception, named here so nobody has to guess whether it was an accident: the runner's own
# bookkeeping table cannot be a migration, because it is what records that a migration ran.
THE_RUNNERS_OWN_TABLE = (PACKAGE_ROOT / "log" / "store" / "postgres.py").relative_to(ROOT)


def test_every_migration_is_numbered_so_the_order_they_apply_in_is_the_order_they_read_in() -> None:
    """A migration is added, never edited, and name order is the only order there is."""
    names = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    assert names == [name for name in names if re.match(r"^\d{4}_[a-z_]+\.sql$", name)]
    assert [name[:4] for name in names] == [f"{n:04d}" for n in range(1, len(names) + 1)]


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
