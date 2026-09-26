"""How an extension is found: named in PINECALL_EXTENSIONS, imported, asked to register itself."""

from __future__ import annotations

from importlib import import_module

from pinecall.errors import PinecallError
from pinecall.extensions.points import Extensions

# The one name a package must export. It is called once with the gateway's Extensions and fills
# the points it has a policy for; the rest keep the runtime's answer.
REGISTER = "register"


class NoSuchExtension(PinecallError):
    """A package the box was told to load is not there, or has nothing to register."""


def extensions_from(named: str) -> Extensions:
    """The points, filled by every package `named` lists — or left as the runtime answers.

    A name that does not import is raised, not skipped: a box that was told to load a policy and
    quietly ran without one would admit every org without limits, and nobody would know. `named`
    is PINECALL_EXTENSIONS as the settings read it: the loader takes the one string it reads.
    """
    extensions = Extensions()
    for name in named_in(named):
        try:
            module = import_module(name)
        except ImportError as missing:
            raise NoSuchExtension(
                f"PINECALL_EXTENSIONS names {name!r}, which does not import: {missing}"
            ) from missing
        register = getattr(module, REGISTER, None)
        if not callable(register):
            raise NoSuchExtension(f"{name!r} has no `{REGISTER}(extensions)`: nothing to plug in")
        register(extensions)
    return extensions


def named_in(setting: str) -> tuple[str, ...]:
    """The package names one setting carries, comma separated, blanks dropped."""
    return tuple(one.strip() for one in setting.split(",") if one.strip())
