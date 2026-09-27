"""Fixture: every suppression."""

import os  # noqa: F401

x: int = "a"  # type: ignore[assignment]
y: int = "b"  # pyright: ignore[reportAssignmentType]


def never() -> None:  # pragma: no cover
    """Never."""
