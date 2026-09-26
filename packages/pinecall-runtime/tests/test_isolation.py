"""Which package may import which, read off every import in the tree with ast, never a regex."""

from collections.abc import Sequence

import pytest

from tests.tree import (
    CORE_ROOT,
    PACKAGE_ROOT,
    SOURCE_ROOTS,
    PythonModule,
    modules_under,
    package_dir,
)

pytestmark = pytest.mark.unit

# The seams. A package earns its directory by having a line here; a package that is not listed
# imports nothing of ours. The root's own modules (_settings, errors) and the generated wire
# (pinecall_protocol) are everybody's except types', which imports nothing but the root error.
MAY_IMPORT: dict[str, frozenset[str]] = {
    "types": frozenset(),
    "extensions": frozenset({"types"}),
    "auth": frozenset({"types", "log"}),
    "log": frozenset({"types"}),
    "providers": frozenset({"types"}),
    "orgs": frozenset({"types", "log"}),
    # The outbox reads the mailbox an org wired and records what came of the letter on its row.
    "mail": frozenset({"types", "orgs"}),
    "routes": frozenset({"types", "log"}),
    "tokens": frozenset({"types", "log", "auth"}),
    "session": frozenset({"types", "log", "providers"}),
    "whatsapp": frozenset({"types", "log", "session", "routes", "providers"}),
    "evals": frozenset({"types", "auth", "log", "session", "providers"}),
    "memory": frozenset({"types", "log", "providers"}),
    "knowledge": frozenset({"types", "log", "providers"}),
    "lookups": frozenset({"types", "log", "providers", "memory", "knowledge"}),
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
            "memory",
            "knowledge",
            "lookups",
            "fleet",
            "extensions",
            "mail",
        }
    ),
    "fleet": frozenset({"types"}),
    "worker": frozenset(
        {
            "types",
            "auth",
            "log",
            "providers",
            "orgs",
            "routes",
            "tokens",
            "session",
            "evals",
            "fleet",
        }
    ),
}

# The packages that hold the ideas: no HTTP, no media plane, no driver. livekit is on the list
# with the rest, so a shape that needs the library has moved out of types/ and into session/.
PACKAGES_THAT_HOLD_NO_FRAMEWORK = ["types", "extensions", "log"]
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
        for module in modules_under(package_dir(package))
        if (named := _our_packages_named_by(module)) - allowed
    }
    assert not offenders, f"pinecall/{package} reaches past its line: {offenders}"


# pinecall-core installs without the runtime (packages/pinecall-core): a module of it that named
# the runtime would import on a laptop, where the two sit side by side, and break on the first
# machine that installed the core alone — cloud's, or anybody's extension.
THE_CORE = frozenset({"types", "extensions", "errors"})
WHAT_THE_CORE_NEVER_INSTALLS = [*FRAMEWORKS, "pydantic_settings", "pinecall_protocol"]


def test_the_core_imports_nothing_of_the_runtime() -> None:
    """Every `pinecall.*` a core module names is one of its own three: the root's modules too."""
    offenders = {
        str(module.path): sorted(named - THE_CORE)
        for module in modules_under(CORE_ROOT)
        if (named := _pinecall_modules_named_by(module)) - THE_CORE
    }
    assert not offenders, f"pinecall-core reaches into the runtime: {offenders}"


@pytest.mark.parametrize("library", WHAT_THE_CORE_NEVER_INSTALLS)
def test_the_core_imports_no_library_it_does_not_declare(library: str) -> None:
    """Its dependencies are the standard library: a framework here is a dependency it lacks."""
    offenders = _the_modules_that_import(modules_under(CORE_ROOT), library)
    assert not offenders, f"pinecall-core imports {library}: {offenders}"


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


# What every package may import: a module at the root (`pinecall._settings`, `pinecall._version`),
# and the root error — a package and not a module only so that its py.typed can ship, since a
# namespace's root can carry no marker (PEP 561). Only the rest has a line in the table.
EVERYBODYS = frozenset(path.stem for root in SOURCE_ROOTS for path in root.glob("*.py")) | {
    "errors"
}


def _pinecall_modules_named_by(module: PythonModule) -> set[str]:
    """Every first name under `pinecall.` a module imports: `pinecall.log.reduce` names `log`."""
    return {name.split(".")[1] for name in module.imported_modules if name.startswith("pinecall.")}


def _our_packages_named_by(module: PythonModule) -> set[str]:
    """The same, minus what is everybody's: what the table above has a line for."""
    return _pinecall_modules_named_by(module) - EVERYBODYS


def _the_modules_the_rule_speaks_about(package: str, framework: str) -> Sequence[PythonModule]:
    """Every module of the package, minus the one exception, and only for the driver it holds."""
    modules = modules_under(package_dir(package))
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
    return tuple(module for module in _every_source_module() if module.path not in excused)


def _the_modules_outside(package: str) -> tuple[PythonModule, ...]:
    """Every module of the runtime that does not live under pinecall.<package>."""
    inside = {module.path for module in modules_under(package_dir(package))}
    return tuple(module for module in _every_source_module() if module.path not in inside)


def _every_source_module() -> tuple[PythonModule, ...]:
    """Both portions of the namespace: the runtime's source and the core's."""
    return tuple(module for root in SOURCE_ROOTS for module in modules_under(root))
