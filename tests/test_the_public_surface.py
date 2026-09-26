"""What a stranger may import: the root and every package export their API and nothing else."""

import importlib

import pytest

import pinecall
from pinecall._settings import load_settings

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


def test_the_root_exports_the_version_and_the_root_error_and_nothing_else() -> None:
    assert pinecall.__all__ == ["PinecallError", "__version__"]
    assert isinstance(pinecall.__version__, str)


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
