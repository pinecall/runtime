"""`pinecall-runtime doctor`: one line per check, ✓ or ✗ with the reason, and what is down first."""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from pinecall._settings import Settings, env_files_read, load_settings, variable_of
from pinecall.cli.doctor.probes import Probes, live_probes

PURPOSE: str = "keys present · livekit · postgres · tei · lk"

# pgvector installs under the name `vector`; the BM25 half of the search stack installs under
# `pg_textsearch`. Both come from the Postgres image infra/compose/dev.yml runs.
REQUIRED_EXTENSIONS: tuple[str, ...] = ("vector", "pg_textsearch")

# A call needs one key of each role. The doctor reads presence and never, ever a value.
PROVIDER_KEYS: dict[str, tuple[str, ...]] = {
    "llm": ("anthropic_api_key", "openai_api_key"),
    "stt": ("soniox_api_key", "deepgram_api_key"),
    "tts": ("eleven_api_key",),
}

BENCH_NOT_WIRED = "bench: no embedder wired yet — it lands in ms-9"

# The LiveKit CLI is how a person reads current documentation and manages trunks and dispatch
# (`lk docs`, `lk sip`, `lk dispatch`) — livekit's own starter tells its agent to ask for it. It is
# a tool on the machine, never a dependency of a call, so its absence is advice and not an outage.
LIVEKIT_CLI = "lk"
HOW_TO_INSTALL_LIVEKIT_CLI = "brew install livekit-cli"
WHAT_LIVEKIT_CLI_IS_FOR = "lk docs · lk sip · lk dispatch"


@dataclass(frozen=True)
class Result:
    """One line of the report: what was asked, whether it answered, and why."""

    name: str
    ok: bool
    detail: str
    # A ✗ that is advice, not an outage: the box still carries a call without it, so it is
    # reported and never made the verdict.
    advisory: bool = False


# A check asks one service one question. Adding one is a function plus a line in CHECKS.
type Check = Callable[[Settings, Probes], Result]


def configure(parser: argparse.ArgumentParser) -> None:
    """One flag and no verbs: the doctor either reports, or reports and measures."""
    parser.add_argument(
        "--bench",
        action="store_true",
        help="also measure the embedder — from ms-9",
    )
    parser.set_defaults(run=run)


def run(arguments: argparse.Namespace) -> int:
    """Name the env file first, then walk the checks, print the report, answer with the verdict."""
    print(render_env_source())
    print()
    results = run_checks(load_settings(), live_probes())
    print(render_report(results))
    if arguments.bench:
        print(BENCH_NOT_WIRED)
    return 1 if first_failure(results) else 0


def render_env_source() -> str:
    """The report's first line: which .env was read, so an ignored one can never be silent."""
    files = env_files_read()
    if not files:
        return "env: no .env — environment only"
    read_in_order = ", ".join(str(path) for path in files)
    return f"env: {read_in_order}" + (" — the last one wins" if len(files) > 1 else "")


def run_checks(settings: Settings, probes: Probes) -> list[Result]:
    """Every check runs, even after one fails: stopping at the first ✗ hides the rest of the box."""
    return [check(settings, probes) for check in CHECKS]


def render_report(results: Sequence[Result]) -> str:
    """The checks in order, then the verdict: `all up`, or the first thing that is down."""
    width = max(len(result.name) for result in results)
    lines = [f"{_mark(result)} {result.name.ljust(width)}  {result.detail}" for result in results]
    down = first_failure(results)
    lines.append("")
    lines.append(f"first down: {down.name} — {down.detail}" if down else "all up")
    return "\n".join(lines)


def first_failure(results: Sequence[Result]) -> Result | None:
    """The check the exit code is about. None when everything the box needs answered."""
    return next((result for result in results if not result.ok and not result.advisory), None)


def _mark(result: Result) -> str:
    """✓ answered · ✗ down · ! missing but not needed to carry a call."""
    if result.ok:
        return "✓"
    return "!" if result.advisory else "✗"


def check_provider_keys(settings: Settings, _probes: Probes) -> Result:
    """Presence by role, under the vendor's own variable name. A value is never read or printed."""
    present: list[str] = []
    missing: list[str] = []
    for role, fields in PROVIDER_KEYS.items():
        set_here = [variable_of(field) for field in fields if getattr(settings, field)]
        if set_here:
            present.append(f"{role} {', '.join(set_here)}")
        else:
            wanted = " or ".join(variable_of(field) for field in fields)
            missing.append(f"{role} (set {wanted})")
    if missing:
        return Result("provider keys", False, "nothing for " + " · ".join(missing))
    return Result("provider keys", True, " · ".join(present))


def check_livekit_is_reachable(settings: Settings, probes: Probes) -> Result:
    """LiveKit serves its HTTP endpoint and the WebSocket on one port, so one setting names both."""
    url = _http_url_of(settings.livekit_url)
    try:
        status = probes.http_status(url)
    except Exception as failure:
        return Result("livekit", False, f"{url} — {_reason(failure)}")
    return Result("livekit", True, f"{url} — HTTP {status}")


def check_postgres_is_ready(settings: Settings, probes: Probes) -> Result:
    """Reachable is half of it: without both extensions the search stack has nowhere to live."""
    shown = _without_password(settings.database_url)
    try:
        installed = probes.postgres_extensions(settings.database_url)
    except Exception as failure:
        return Result("postgres", False, f"{shown} — {_reason(failure)}")
    absent = [name for name in REQUIRED_EXTENSIONS if name not in installed]
    if absent:
        return Result("postgres", False, f"{shown} — no extension {', '.join(absent)}")
    return Result("postgres", True, f"{shown} — {', '.join(REQUIRED_EXTENSIONS)}")


def check_tei_is_reachable(settings: Settings, probes: Probes) -> Result:
    """TEI answers /info with the model it loaded; anything but 200 means it is serving nothing."""
    url = f"{settings.tei_url.rstrip('/')}/info"
    try:
        status = probes.http_status(url)
    except Exception as failure:
        return Result("tei", False, f"{url} — {_reason(failure)}")
    if status != 200:
        return Result("tei", False, f"{url} — HTTP {status}")
    return Result("tei", True, f"{url} — HTTP {status}")


def check_the_livekit_cli_is_installed(_settings: Settings, probes: Probes) -> Result:
    """Found or not, and where. The doctor looks on the PATH and never runs it."""
    found = probes.executable_path(LIVEKIT_CLI)
    if found is None:
        return Result(
            LIVEKIT_CLI,
            False,
            f"not installed — {HOW_TO_INSTALL_LIVEKIT_CLI} ({WHAT_LIVEKIT_CLI_IS_FOR})",
            advisory=True,
        )
    return Result(LIVEKIT_CLI, True, f"{found} — {WHAT_LIVEKIT_CLI_IS_FOR}")


# The order the report reads, and the first ✗ in it is the one the verdict names. `lk` is last
# because it is the only line that cannot make the verdict.
CHECKS: tuple[Check, ...] = (
    check_provider_keys,
    check_livekit_is_reachable,
    check_postgres_is_ready,
    check_tei_is_reachable,
    check_the_livekit_cli_is_installed,
)


def _http_url_of(livekit_url: str) -> str:
    """ws:// and wss:// name the same server as http:// and https://: one setting, not two."""
    parts = urlsplit(livekit_url)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    return urlunsplit((scheme, parts.netloc, parts.path or "/", "", ""))


def _without_password(dsn: str) -> str:
    """A report is read out loud and pasted into issues; the password never travels with it."""
    parts = urlsplit(dsn)
    if parts.password is None:
        return dsn
    host = _bracketed(parts.hostname or "")
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username}@{host}" if parts.username else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _bracketed(host: str) -> str:
    """urlsplit hands back an IPv6 host without its brackets, and `::1:5432` is not an address."""
    return f"[{host}]" if ":" in host else host


def _reason(failure: Exception) -> str:
    """`ConnectionRefusedError: [Errno 61] Connection refused` reads at a glance in a terminal."""
    message = str(failure).strip()
    return f"{type(failure).__name__}: {message}" if message else type(failure).__name__
