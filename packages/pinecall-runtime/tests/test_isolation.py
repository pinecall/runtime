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
# imports nothing of ours. What EVERYBODYS names below (the version, settings, errors) and the
# generated wire (pinecall_protocol) are everybody's except types', which imports only the error.
MAY_IMPORT: dict[str, frozenset[str]] = {
    "types": frozenset(),
    # The database: the driver, the pool, the migrations. It reads no package of ours.
    "db": frozenset(),
    # The configuration reads the environment into the shapes it names, and nothing else.
    "settings": frozenset({"types"}),
    "extensions": frozenset({"types"}),
    "fleet": frozenset(),
    "providers": frozenset({"types"}),
    "log": frozenset({"db", "types"}),
    "auth": frozenset({"db", "types"}),
    "orgs": frozenset({"db", "log", "types"}),
    "routes": frozenset({"db", "types"}),
    "tokens": frozenset({"auth", "db", "log", "types"}),
    # The outbox reads the mailbox an org wired and records what came of the letter on its row.
    "mail": frozenset({"orgs", "types"}),
    "session": frozenset({"log", "providers", "types"}),
    "whatsapp": frozenset({"log", "routes", "session", "types"}),
    "evals": frozenset({"auth", "db", "log", "providers", "session", "types"}),
    "memory": frozenset({"db", "log", "providers", "types"}),
    "knowledge": frozenset({"db", "providers", "types"}),
    "lookups": frozenset({"knowledge", "log", "memory", "types"}),
    "api": frozenset(
        {
            "auth",
            "db",
            "evals",
            "extensions",
            "fleet",
            "knowledge",
            "log",
            "lookups",
            "mail",
            "memory",
            "orgs",
            "providers",
            "routes",
            "session",
            "tokens",
            "types",
            "whatsapp",
        }
    ),
    "worker": frozenset({"auth", "evals", "fleet", "log", "providers", "session", "types"}),
}

# The packages that hold the ideas: no HTTP, no media plane, no driver. livekit is on the list
# with the rest, so a shape that needs the library has moved out of types/ and into session/.
PACKAGES_THAT_HOLD_NO_FRAMEWORK = ["types", "extensions", "log"]
FRAMEWORKS = ["fastapi", "livekit", "uvicorn", "asyncpg"]

# Each vendor by its own SDK name, so a stray import reads as what it is: a vendor in the core.
VENDOR_SDKS = ["anthropic", "openai", "soniox", "deepgram", "elevenlabs"]

# The database's one door: the driver is named under db/ and nowhere else, so every store — the
# log's, the orgs', auth's — asks db/ for its pool and none of them ever names asyncpg.
THE_DRIVERS_DOOR = PACKAGE_ROOT / "db"
THE_DRIVER = "asyncpg"


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


# The table is read as documentation (ARCHITECTURE.md §11 is the same table), so it may not say a
# package reaches another it no longer does: a line that allows more than the code imports is a
# seam described wider than it is, and the next import through it goes unnoticed.
@pytest.mark.parametrize("package", sorted(MAY_IMPORT))
def test_every_line_of_the_table_is_used_and_nothing_more(package: str) -> None:
    used: set[str] = set()
    for module in modules_under(package_dir(package)):
        used |= _our_packages_named_by(module)
    unused = sorted(MAY_IMPORT[package] - used - {package})
    assert not unused, f"pinecall/{package}'s line allows {unused}, which it does not import"


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize("package", PACKAGES_THAT_HOLD_NO_FRAMEWORK)
def test_a_module_under_a_pure_package_never_imports_a_framework(
    package: str, framework: str
) -> None:
    offenders = _the_modules_that_import(modules_under(package_dir(package)), framework)
    assert not offenders, f"pinecall/{package} imports {framework}: {offenders}"


def test_the_driver_is_named_nowhere_but_db() -> None:
    """The rule the whole runtime lives under: a store, a CLI, the api and the worker ask db/."""
    offenders = _the_modules_that_import(_the_modules_outside_db(), THE_DRIVER)
    assert not offenders, f"{THE_DRIVER} is imported outside db/: {offenders}"


def test_only_providers_imports_a_livekit_plugin() -> None:
    """A plugin is the vendor's own code wearing a LiveKit name; only providers/ may say it."""
    offenders = _the_modules_that_import(_the_modules_outside("providers"), "livekit.plugins")
    assert not offenders, f"livekit.plugins is imported outside pinecall/providers: {offenders}"


@pytest.mark.parametrize("vendor", VENDOR_SDKS)
def test_only_providers_imports_a_vendor_sdk(vendor: str) -> None:
    """The core asks providers/ for a capability; it never learns whose capability it is."""
    offenders = _the_modules_that_import(_the_modules_outside("providers"), vendor)
    assert not offenders, f"{vendor} is imported outside pinecall/providers: {offenders}"


# What every package may import: the version at the root, the configuration, and the root error
# (a package and not a module only so that its py.typed can ship, since a namespace's root carries
# no marker, PEP 561). Their own imports are still held to their lines above.
EVERYBODYS = frozenset(path.stem for root in SOURCE_ROOTS for path in root.glob("*.py")) | {
    "errors",
    "settings",
}


def _pinecall_modules_named_by(module: PythonModule) -> set[str]:
    """Every first name under `pinecall.` a module imports: `pinecall.log.reduce` names `log`."""
    return {name.split(".")[1] for name in module.imported_modules if name.startswith("pinecall.")}


def _our_packages_named_by(module: PythonModule) -> set[str]:
    """The same, minus what is everybody's: what the table above has a line for."""
    return _pinecall_modules_named_by(module) - EVERYBODYS


def _the_modules_that_import(modules: Sequence[PythonModule], package: str) -> list[str]:
    """The paths of the modules that name `package`, ready to read in a failure message."""
    return [str(module.path) for module in modules if module.imports(package)]


def _the_modules_outside_db() -> tuple[PythonModule, ...]:
    """Every module of the runtime and the core that is not the driver's one door."""
    excused = {module.path for module in modules_under(THE_DRIVERS_DOOR)}
    return tuple(module for module in _every_source_module() if module.path not in excused)


def _the_modules_outside(package: str) -> tuple[PythonModule, ...]:
    """Every module of the runtime that does not live under pinecall.<package>."""
    inside = {module.path for module in modules_under(package_dir(package))}
    return tuple(module for module in _every_source_module() if module.path not in inside)


def _every_source_module() -> tuple[PythonModule, ...]:
    """Both portions of the namespace: the runtime's source and the core's."""
    return tuple(module for root in SOURCE_ROOTS for module in modules_under(root))
