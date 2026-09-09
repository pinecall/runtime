"""The cli suite reads the same dev Postgres the log suite writes to, through its fixtures."""

from tests.postgres import Dev, postgres

__all__ = ["Dev", "postgres"]
