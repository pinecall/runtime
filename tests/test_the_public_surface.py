"""What a stranger may import: the root and every package export their API and nothing else."""

import importlib

import pytest

from pinecall._settings import load_settings
from pinecall._version import __version__
from pinecall.errors import PinecallError
from tests.tree import SOURCE_ROOTS

pytestmark = pytest.mark.unit

PACKAGES = [
    "auth",
    "evals",
    "extensions",
    "fleet",
    "knowledge",
    "log",
    "lookups",
    "mail",
    "memory",
    "types",
    "worker",
]


# `pinecall` is a namespace both distributions install into (the runtime and pinecall-core): an
# __init__.py in either one makes that one the whole of `pinecall`, and every module of the other
# stops importing — on the first machine that has both, never in a checkout that has one.
def test_the_namespace_has_no_root_module_in_either_distribution() -> None:
    stray = [str(root / "__init__.py") for root in SOURCE_ROOTS if (root / "__init__.py").exists()]
    assert not stray, f"pinecall is a namespace package: {stray} must not exist"


def test_the_root_error_and_the_version_are_named_modules() -> None:
    assert issubclass(PinecallError, Exception)
    assert isinstance(__version__, str)


@pytest.mark.parametrize("name", PACKAGES)
def test_every_package_imports_says_what_it_is_and_pins_its_surface(name: str) -> None:
    module = importlib.import_module(f"pinecall.{name}")
    assert module.__doc__, f"pinecall.{name} opens with a one-line docstring"
    assert module.__all__ == sorted(module.__all__, key=_constants_first)


def test_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINECALL_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LIVEKIT_URL", "ws://livekit.example:7880")
    settings = load_settings()
    assert settings.log_level == "DEBUG"
    assert settings.livekit_url == "ws://livekit.example:7880"


def test_a_unit_test_never_sees_a_real_key() -> None:
    settings = load_settings()
    assert settings.anthropic_api_key == "sk-ant-dead-sentinel"
    assert settings.livekit_url == "ws://127.0.0.1:1"


def _constants_first(name: str) -> tuple[int, str]:
    """The order ruff's isort keeps an __all__ in: CONSTANTS, then Classes, then functions."""
    if name.isupper():
        return (0, name)
    if name[0].isupper():
        return (1, name)
    return (2, name)
