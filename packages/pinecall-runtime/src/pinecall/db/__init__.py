"""The database: the one door to the driver, the pool every store shares, and the migrations."""

from pinecall.db.connecting import (
    DEFAULT_SCHEMA,
    SchemaNameRefused,
    StoreUnreachable,
    create_pool,
    installed_extensions,
    open_pool,
    search_path_of,
    without_password,
)
from pinecall.db.migrating import (
    MIGRATIONS,
    MigrationsRefused,
    apply_migrations,
    migrations_behind,
)
from pinecall.db.pool import Connection, Pool

__all__ = [
    "DEFAULT_SCHEMA",
    "MIGRATIONS",
    "Connection",
    "MigrationsRefused",
    "Pool",
    "SchemaNameRefused",
    "StoreUnreachable",
    "apply_migrations",
    "create_pool",
    "installed_extensions",
    "migrations_behind",
    "open_pool",
    "search_path_of",
    "without_password",
]
