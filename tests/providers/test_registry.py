"""The registry itself: a file is a vendor, a decorator is the whole of the bookkeeping."""

import sys
from importlib import import_module
from pathlib import Path
from typing import cast

import pytest

from pinecall._settings import Settings
from pinecall.providers.registry import Asked, NoProvider, Vendors, a_key
from tests.providers.vendors import VENDORS

pytestmark = pytest.mark.unit


def an_ask(model: str | None = None) -> Asked:
    """A question for the fake modality: no keys are needed to answer it."""
    return Asked(settings=Settings(), model=model)


# Criterion 3: tests/providers/vendors/acme.py is a whole vendor. Nothing imports it, no table
# lists it, and no __init__ mentions it — it is found because it is in the package.
def test_a_vendor_file_nothing_imports_is_still_found_by_its_name() -> None:
    assert VENDORS.names == ("acme",)
    assert VENDORS.build("acme", an_ask("acme-two")) == "acme-two"


def test_a_vendor_answers_with_its_own_default_when_no_model_was_asked_for() -> None:
    """The default model of a modality lives in the vendor file and nowhere else."""
    assert VENDORS.build("acme", an_ask()) == "acme-the-only-one"


def test_a_vendor_this_build_does_not_have_is_refused_with_the_ones_it_does() -> None:
    with pytest.raises(NoProvider, match="no fake vendor named 'zenith'"):
        VENDORS.build("zenith", an_ask())


def test_the_vendors_package_is_read_by_import_and_not_by_a_list_of_names() -> None:
    """Discovery is an import: after one build, the vendor's own module is in sys.modules."""
    VENDORS.build("acme", an_ask())
    assert "tests.providers.vendors.acme" in sys.modules


# Which key a_key hands back, and whose, is tests/providers/test_provider_keys.py's subject.
def test_a_vendor_with_no_key_is_refused_now_and_not_mid_call() -> None:
    with pytest.raises(NoProvider, match="acme has no API key in this process"):
        a_key("acme", an_ask())


# The console crash left a table marked read and empty: the setup hook imported the vendors on
# livekit's job thread, a plugin refused to register there, and every later call in that process
# resolved no vendor at all. A table is read once, and once means once successfully.
def test_a_package_that_raises_the_first_time_is_read_again_and_ends_with_the_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vendors = _a_package_whose_first_import_raises(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="this plugin wants the main thread"):
        vendors.build("flaky", an_ask())

    assert vendors.build("flaky", an_ask()) == "flaky-built"
    assert vendors.names == ("flaky",)


def test_the_first_call_raises_instead_of_answering_out_of_an_empty_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure is loud where it happens, and never a NoProvider naming 'no vendor at all'."""
    vendors = _a_package_whose_first_import_raises(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError):
        vendors.names  # noqa: B018 — reading the names is what fills the table


# A vendor package that fails once and works the second time, written to disk: the module body
# raises until the file beside it says it has already been tried.
def _a_package_whose_first_import_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Vendors[str]:
    """A two-file package on sys.path whose only vendor file raises on its first execution."""
    package = tmp_path / "flakyvendors"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from pinecall.providers.registry import Vendors\n\nVENDORS: Vendors[str] = "
        'Vendors("fake", __name__)\n'
    )
    (package / "flaky.py").write_text(
        "from pathlib import Path\n\n"
        "from flakyvendors import VENDORS\n\n"
        'tried = Path(__file__).with_name("tried")\n'
        "if not tried.exists():\n"
        '    tried.write_text("once")\n'
        '    raise RuntimeError("this plugin wants the main thread")\n\n\n'
        '@VENDORS.registers("flaky")\n'
        "def build(asked: object) -> str:\n"
        '    return "flaky-built"\n'
    )
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    monkeypatch.delitem(sys.modules, "flakyvendors", raising=False)
    monkeypatch.delitem(sys.modules, "flakyvendors.flaky", raising=False)
    vendors = import_module("flakyvendors").VENDORS
    assert isinstance(vendors, Vendors)
    return cast(Vendors[str], vendors)
