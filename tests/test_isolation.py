"""Which package may import which, read off every import in the tree with ast, never a regex."""

from collections.abc import Sequence

import pytest

from tests.tree import PACKAGE_ROOT, PythonModule, modules_under

pytestmark = pytest.mark.unit

# The seams. A package earns its directory by having a line here; a package that is not listed
# imports nothing of ours. The root's private modules (_settings, _exceptions) and the generated
# wire (pinecall_protocol) are everybody's except types', which imports nothing but the root error.
MAY_IMPORT: dict[str, frozenset[str]] = {
    "types": frozenset(),
    "auth": frozenset({"types"}),
    "log": frozenset({"types"}),
    "providers": frozenset({"types"}),
    "orgs": frozenset({"types", "log"}),
    "routes": frozenset({"types", "log"}),
    "tokens": frozenset({"types", "log", "auth"}),
    "session": frozenset({"types", "log", "providers"}),
    "whatsapp": frozenset({"types", "log", "session", "routes", "providers"}),
    "evals": frozenset({"types", "log", "session", "providers"}),
    "api": frozenset(
        {
            "types",
            "auth",
            "log",
            "providers",
            "orgs",
            "routes",
            "tokens",
            "session",
            "whatsapp",
            "evals",
        }
    ),
    "worker": frozenset(
        {"types", "auth", "log", "providers", "orgs", "routes", "tokens", "session", "evals"}
    ),
}

# The packages that hold the ideas: no HTTP, no media plane, no driver. livekit is on the list
# with the rest, so a shape that needs the library has moved out of types/ and into session/.
PACKAGES_THAT_HOLD_NO_FRAMEWORK = ["types", "log"]
FRAMEWORKS = ["fastapi", "livekit", "uvicorn", "asyncpg"]

# Each vendor by its own SDK name, so a stray import reads as what it is: a vendor in the core.
VENDOR_SDKS = ["anthropic", "openai", "soniox", "deepgram", "elevenlabs"]

# The one exception, named so nobody has to guess whether it was an accident: the store adapter
# is the one door to IO, so asyncpg lives behind it and the log's rules never see a driver.
THE_STORE_ADAPTER = PACKAGE_ROOT / "log" / "store"
THE_DRIVER_IT_MAY_HOLD = "asyncpg"


@pytest.mark.parametrize("package", sorted(MAY_IMPORT))
def test_a_package_imports_only_the_packages_its_line_names(package: str) -> None:
    allowed = MAY_IMPORT[package] | {package}
    offenders = {
        str(module.path): sorted(named - allowed)
        for module in modules_under(PACKAGE_ROOT / package)
        if (named := _our_packages_named_by(module)) - allowed
    }
    assert not offenders, f"pinecall/{package} reaches past its line: {offenders}"


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize("package", PACKAGES_THAT_HOLD_NO_FRAMEWORK)
def test_a_module_under_a_pure_package_never_imports_a_framework(
    package: str, framework: str
) -> None:
    judged = _the_modules_the_rule_speaks_about(package, framework)
    offenders = _the_modules_that_import(judged, framework)
    assert not offenders, f"pinecall/{package} imports {framework}: {offenders}"


def test_the_driver_is_named_nowhere_but_the_store_adapter() -> None:
    """The rule the whole runtime lives under: a CLI, the api and the worker ask the store."""
    offenders = _the_modules_that_import(
        _the_modules_outside_the_store_adapter(), THE_DRIVER_IT_MAY_HOLD
    )
    assert not offenders, f"{THE_DRIVER_IT_MAY_HOLD} is imported outside log/store/: {offenders}"


def test_only_providers_imports_a_livekit_plugin() -> None:
    """A plugin is the vendor's own code wearing a LiveKit name; only providers/ may say it."""
    offenders = _the_modules_that_import(_the_modules_outside("providers"), "livekit.plugins")
    assert not offenders, f"livekit.plugins is imported outside pinecall/providers: {offenders}"


@pytest.mark.parametrize("vendor", VENDOR_SDKS)
def test_only_providers_imports_a_vendor_sdk(vendor: str) -> None:
    """The core asks providers/ for a capability; it never learns whose capability it is."""
    offenders = _the_modules_that_import(_the_modules_outside("providers"), vendor)
    assert not offenders, f"{vendor} is imported outside pinecall/providers: {offenders}"


def _our_packages_named_by(module: PythonModule) -> set[str]:
    """The pinecall packages a module imports: `pinecall.log.reduce` names `log`."""
    return {
        name.split(".")[1]
        for name in module.imported_modules
        if name.startswith("pinecall.") and not name.split(".")[1].startswith("_")
    }


def _the_modules_the_rule_speaks_about(package: str, framework: str) -> Sequence[PythonModule]:
    """Every module of the package, minus the one exception, and only for the driver it holds."""
    modules = modules_under(PACKAGE_ROOT / package)
    if framework != THE_DRIVER_IT_MAY_HOLD:
        return modules
    excused = {module.path for module in modules_under(THE_STORE_ADAPTER)}
    return [module for module in modules if module.path not in excused]


def _the_modules_that_import(modules: Sequence[PythonModule], package: str) -> list[str]:
    """The paths of the modules that name `package`, ready to read in a failure message."""
    return [str(module.path) for module in modules if module.imports(package)]


def _the_modules_outside_the_store_adapter() -> tuple[PythonModule, ...]:
    """Every module of the runtime that is not the one door to the driver."""
    excused = {module.path for module in modules_under(THE_STORE_ADAPTER)}
    return tuple(module for module in modules_under(PACKAGE_ROOT) if module.path not in excused)


def _the_modules_outside(package: str) -> tuple[PythonModule, ...]:
    """Every module of the runtime that does not live under pinecall.<package>."""
    inside = {module.path for module in modules_under(PACKAGE_ROOT / package)}
    return tuple(module for module in modules_under(PACKAGE_ROOT) if module.path not in inside)
