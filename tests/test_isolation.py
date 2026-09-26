"""Which package may import which, read off every import in the tree with ast, never a regex."""

import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest

from pinecall_testkit.tree import (
    CORE_ROOT,
    DISTRIBUTIONS,
    SOURCE_ROOTS,
    PythonModule,
    modules_under,
    package_dir,
    package_of,
    source_modules,
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
    "fleet": frozenset({"types"}),
    "providers": frozenset({"types"}),
    "log": frozenset({"db", "types"}),
    "auth": frozenset({"db", "types"}),
    "orgs": frozenset({"db", "log", "providers", "types"}),
    "routes": frozenset({"db", "types"}),
    "tokens": frozenset({"auth", "db", "log", "types"}),
    # The outbox reads the mailbox an org wired and records what came of the letter on its row.
    "mail": frozenset({"orgs", "types"}),
    "session": frozenset({"log", "providers", "types"}),
    "whatsapp": frozenset({"log", "routes", "session", "types"}),
    "evals": frozenset({"db", "log", "providers", "session", "tokens", "types"}),
    "memory": frozenset({"db", "log", "providers", "types"}),
    "knowledge": frozenset({"db", "providers", "types"}),
    "lookups": frozenset({"knowledge", "log", "memory", "types"}),
    # What a person does with an account, across the domains it touches: an org founded at sign-up.
    "accounts": frozenset({"auth", "extensions", "mail", "orgs", "types"}),
    # Placing a call out: the guards, the log and the job, across the domains a dial touches.
    "telephony": frozenset({"log", "orgs", "routes", "session", "types"}),
    # What this gateway process holds right now: the sockets, their agents and doors, the calls.
    "live": frozenset({"evals", "log", "lookups", "orgs", "providers", "session", "types"}),
    "api": frozenset(
        {
            "accounts",
            "auth",
            "db",
            "telephony",
            "evals",
            "extensions",
            "fleet",
            "knowledge",
            "live",
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
    "worker": frozenset({"evals", "fleet", "log", "providers", "session", "types"}),
}

# The packages that hold the ideas: no HTTP, no media plane, no driver. livekit is on the list
# with the rest, so a shape that needs the library has moved out of types/ and into session/.
PACKAGES_THAT_HOLD_NO_FRAMEWORK = ["types", "extensions", "log"]
FRAMEWORKS = ["fastapi", "livekit", "uvicorn", "asyncpg"]

# The HTTP framework is the doors' and the process that serves them, and nobody else's: a verb
# that raises HTTPException has decided a status the doors decide (api/refusals.py).
THE_HTTP_FRAMEWORK = ["fastapi", "starlette", "uvicorn"]
SERVE_HTTP = ("api", "cli")

# Each vendor by its own SDK name, so a stray import reads as what it is: a vendor in the core.
VENDOR_SDKS = ["anthropic", "openai", "soniox", "deepgram", "elevenlabs"]

# The database's one door: the driver is named under db/ and nowhere else, so every store — the
# log's, the orgs', auth's — asks db/ for its pool and none of them ever names asyncpg.
THE_DRIVERS_DOOR = package_dir("db")
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


@pytest.mark.parametrize("framework", THE_HTTP_FRAMEWORK)
def test_only_the_doors_and_the_cli_import_the_http_framework(framework: str) -> None:
    offenders = [
        str(module.path)
        for module in source_modules()
        if module.imports(framework) and package_of(module) not in SERVE_HTTP
    ]
    assert not offenders, f"{framework} outside api/ and cli/: {offenders}"


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


# Every distribution's pyproject is the table again, one level up: the distributions its
# packages import, and nothing else, pinned — so an import across a distribution nobody declared
# fails here and not on the first machine that installed one wheel without the other.
@pytest.mark.parametrize(
    "distribution",
    [d for d in DISTRIBUTIONS if (d / "src" / "pinecall").is_dir()],
    ids=lambda d: d.name,
)
def test_every_distribution_declares_exactly_the_distributions_its_packages_import(
    distribution: Path,
) -> None:
    owner = {
        package.name: root.parents[1].name
        for root in SOURCE_ROOTS
        for package in root.iterdir()
        if package.is_dir() or package.suffix == ".py"
    }
    source = distribution / "src" / "pinecall"
    imported = {
        owner[name.removesuffix(".py")]
        for module in modules_under(source)
        for name in _pinecall_modules_named_by(module)
        if name.removesuffix(".py") in owner
    } - {distribution.name}
    project = tomllib.loads((distribution / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    declared = {
        requirement.split("==")[0].split(">=")[0]
        for requirement in project.get("dependencies", [])
        if requirement.startswith("pinecall-") and not requirement.startswith("pinecall-protocol")
    }
    assert declared == imported, (
        f"{distribution.name} declares {sorted(declared - imported)} it does not import and "
        f"imports {sorted(imported - declared)} it does not declare"
    )


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
