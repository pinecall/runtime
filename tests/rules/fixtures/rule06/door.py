"""Fixture: a door deciding a status by itself."""

from fastapi import HTTPException


def refuse() -> None:
    """Refuse."""
    raise HTTPException(404)
