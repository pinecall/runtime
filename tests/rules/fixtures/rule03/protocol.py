"""Fixture: a Protocol, an ABC, a cast and a TYPE_CHECKING import."""

from abc import ABC
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from pathlib import Path


class Reads(Protocol):
    """A protocol."""

    def read(self) -> str: ...


class Base(ABC):
    """An abstract base."""


def name(path: "Path") -> str:
    """A cast."""
    return cast("str", path)
