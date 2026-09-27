"""The exceptions the rules allow, each with its file and a one-line reason."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Allowed:
    """One allowed occurrence: the file, the text the rule matches, and why."""

    file: str
    text: str
    why: str


# Rule 3: Protocol, ABC, cast(, TYPE_CHECKING.
PROTOCOLS_AND_CASTS: tuple[Allowed, ...] = ()

# Rule 9: noqa, type: ignore, pyright: ignore, pragma: no cover.
SUPPRESSIONS: tuple[Allowed, ...] = ()

# importlib, getattr on a string.
IMPORTS_BY_NAME: tuple[Allowed, ...] = ()
