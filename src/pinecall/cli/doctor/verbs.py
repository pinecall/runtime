"""`pinecall-runtime doctor`: one line per check, ✓ or ✗ with the reason, and what is down first."""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from pinecall._env_files import env_files_read
from pinecall._settings import Role, Settings, load_settings, variable_of
from pinecall.cli.doctor.probes import Probes, live_probes
from pinecall.providers import catalog
from pinecall.providers.embed import base_url_of, key_field_of, model_of
from pinecall.providers.knocks import KNOCKS
from pinecall.providers.models import DEFAULT_VENDOR
from pinecall.providers.pipeline import DEFAULT_STT, DEFAULT_TTS

PURPOSE: str = "keys present · keys answer · livekit · postgres · embedder · lk"

# pgvector installs under the name `vector`; the BM25 half of the search stack installs under
# `pg_textsearch`. Both come from the Postgres image infra/compose/dev.yml runs.
REQUIRED_EXTENSIONS: tuple[str, ...] = ("vector", "pg_textsearch")


# A call needs one key of each role, and the roles are read off the catalog: every vendor that can
# do that job and has a single-string credential. So a box running on Cartesia reads green without
# anybody adding a row here. The doctor prints a variable's name and never, ever a value.
def provider_keys() -> dict[catalog.Modality, tuple[str, ...]]:
    """Which settings field could answer for each role: every catalogued vendor that does it."""
    return {
        modality: tuple(
            field
            for row in catalog.doing(modality)
            if (field := catalog.settings_field_of(row.name)) is not None
        )
        for modality in catalog.MODALITIES
    }


# What the line says to set when a role has nothing at all. ONE vendor per role — the one this
# runtime runs that role on when an agent declares none — because forty names is not advice, and
# the operator reading this line has no key at all yet. `providers` prints the other forty-four.
OURS: dict[catalog.Modality, str] = {
    "llm": DEFAULT_VENDOR,
    "stt": DEFAULT_STT,
    "tts": DEFAULT_TTS,
}


def the_vendor_worth_naming(modality: catalog.Modality) -> str:
    """The settings field of the vendor this runtime runs that role on when nobody chose one."""
    field = catalog.settings_field_of(OURS[modality])
    assert field is not None, f"providers/catalog.py: {OURS[modality]} has no key to ask for"
    return field


# What a refused key reads in the report, and where the live one goes: the credstore on a box
# (`make secret NAME=… < the key`, from the checkout), the .env on a laptop.
KEY_REFUSED = (
    "refused {variable} (HTTP {status}) — the key is dead: "
    "a box takes a live one with `make secret NAME={variable}`, a laptop in its .env"
)

# An embedder that is down stops no call: a lookup that needs a vector is skipped and the call's
# log says so (`search_skipped`, `recall_skipped`, naming the vendor), and the turn goes on. On
# a laptop, and on the `all` an untouched clone defaults to, that is the whole story — on an
# M-series Mac TEI runs only from the rolling arm64 tag infra/README.md names for TEI_IMAGE.
# Advice, not outage.
EMBEDDER_IS_ADVICE = "a lookup without it is skipped and said in the call's log: this stops no call"

# A HUB is the machine that promised one. It holds the knowledge base and answers the pushes, and
# `PUT /v1/knowledge/{base}` with no embedder is a 503 the tenant reads: nothing about that is
# skipped quietly. So the line is the verdict there, and it names what to type — a deploy that
# ended green over a shut door is the dead ElevenLabs key of 2026-09-09 again.
EMBEDDER_IS_DOWN = "a hub embeds: a knowledge push answers 503 and every lookup is skipped — {fix}"

# There are two shapes of embedder and so two fixes: a container on this box, or a vendor's door
# and the key that opens it. `make secret` is run from the checkout, never on the box by hand.
START_THE_UNIT = "start it with `systemctl start pinecall-tei`, or name a vendor in EMBED_PROVIDER"
BRING_A_LIVE_KEY = "put a live key in with `make secret NAME={variable}`, from the checkout"

# TEI names the model it loaded at /info and answers 200 there. The hosted embedders have no such
# door: what can be asked of them without spending anything is whether the host answers at all,
# and whichever status it answers a keyless GET with is an answer.
TEI_INFO = "/info"

# Which keys this gateway would honour, and the one combination nothing else catches. A dev key is
# the ONLY key a gateway honours — the api_keys table is not read — so a BOX that sets one answers
# every call as org `default` and every real tenant becomes invisible: not a leak, a silence, and a
# silence no other check here would notice. On a laptop it is the whole point, and the line says so.
DEV_KEY_ON_A_BOX = (
    "PINECALL_DEV_KEY is set on a box: it is the only key honoured, so every call is org default "
    "and every tenant is invisible. Unset it and start the gateway on the api_keys table"
)
A_DEV_KEY = (
    "PINECALL_DEV_KEY — one key, org default, the api_keys table not read; the tables are "
    "Postgres's when it answers below. A box unsets it and issues org keys instead"
)
THE_KEYS_TABLE = "the api_keys table — `pinecall-runtime keys issue --org <slug>` mints one"

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
    """No flags and no verbs: the doctor reports."""
    parser.set_defaults(run=run)


def run(arguments: argparse.Namespace) -> int:  # noqa: ARG001 — every verb takes the namespace
    """Name the env file first, then walk the checks, print the report, answer with the verdict."""
    print(render_env_source())
    print()
    results = run_checks(load_settings(), live_probes())
    print(render_report(results))
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
    return [check(settings, probes) for check in checks_for(settings.role)]


# A worker box has no Postgres and no embedder: asking it after them reports two ✗ that are
# the hub's, and a deploy that stopped on them would never finish. The hub is asked everything.
def checks_for(role: Role) -> tuple[Check, ...]:
    """The checks a box of this role is asked, in the order the report reads."""
    if role == "worker":
        return tuple(check for check in CHECKS if check not in ONLY_ON_A_HUB)
    return CHECKS


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
    for role, fields in provider_keys().items():
        set_here = [variable_of(field) for field in fields if getattr(settings, field)]
        if set_here:
            present.append(f"{role} {', '.join(set_here)}")
        else:
            missing.append(f"{role} (set {variable_of(the_vendor_worth_naming(role))})")
    if missing:
        return Result("provider keys", False, "nothing for " + " · ".join(missing))
    return Result("provider keys", True, " · ".join(present))


# Present is not alive: a key that expired, or was pasted wrong, sits in the credstore looking
# exactly like one that works, and the first to know is a caller hearing silence. So every key
# that is set knocks at its vendor's cheapest door, once, with the key where the vendor reads it.
def check_provider_keys_answer(settings: Settings, probes: Probes) -> Result:
    """Each key that is set, knocked at its own vendor: 200 answered, anything else is named."""
    answered: list[str] = []
    down: list[str] = []
    for field, knock in KNOCKS.items():
        key = getattr(settings, field)
        if not key:
            continue
        variable = variable_of(field)
        try:
            status = probes.knock(knock.url, knock.headers(key))
        except Exception as failure:
            down.append(f"{variable} unreachable — {_reason(failure)}")
            continue
        if status == 200:
            answered.append(variable)
        else:
            down.append(KEY_REFUSED.format(variable=variable, status=status))
    if down:
        return Result("provider keys answer", False, " · ".join(down))
    return Result("provider keys answer", True, " · ".join(answered) or "no key is set")


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


# The line says which provider and which model this box embeds with, first, because that is the
# fact a person is usually looking for: a box that retrieves nothing is far more often one running
# the embedder somebody else configured than one whose service is down.
def check_the_embedder_answers(settings: Settings, probes: Probes) -> Result:
    """Which provider and model this box embeds with, its key present, and its door answering."""
    runs = f"{settings.embed_provider} · {model_of(settings)}"
    field = key_field_of(settings)
    if field is not None and not getattr(settings, field):
        return _no_embedder(settings, f"{runs} — no {variable_of(field)}")
    url = _where_the_embedder_answers(settings)
    try:
        status = probes.http_status(url)
    except Exception as failure:
        return _no_embedder(settings, f"{runs} — {url} — {_reason(failure)}")
    if field is None and status != 200:
        return _no_embedder(settings, f"{runs} — {url} — HTTP {status}")
    return Result("embedder", True, f"{runs} — {url} — HTTP {status}")


def _no_embedder(settings: Settings, detail: str) -> Result:
    """On a hub the ✗ is the verdict and says what to type; anywhere else the line is advice."""
    if settings.role != "hub":
        return Result("embedder", False, f"{detail}; {EMBEDDER_IS_ADVICE}", advisory=True)
    down = EMBEDDER_IS_DOWN.format(fix=_the_fix(settings))
    return Result("embedder", False, f"{detail}; {down}")


def _the_fix(settings: Settings) -> str:
    """TEI is a unit on this box; every other provider is a door and a key in the credstore."""
    field = key_field_of(settings)
    if field is None:
        return START_THE_UNIT
    return BRING_A_LIVE_KEY.format(variable=variable_of(field))


def _where_the_embedder_answers(settings: Settings) -> str:
    """TEI's /info, which names the model it loaded; the hosted providers' own base URL."""
    base = base_url_of(settings).rstrip("/")
    return f"{base}{TEI_INFO}" if settings.embed_provider == "tei" else base


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


def check_which_keys_are_honoured(settings: Settings, _probes: Probes) -> Result:
    """Which keys open this gateway's doors — the table, or the one dev key that replaces it."""
    if not settings.dev_key:
        return Result("api keys", True, THE_KEYS_TABLE)
    # A box is a box by what it was told to be, or by having opened the operator API at all.
    if settings.role != "all" or settings.ops_key:
        return Result("api keys", False, DEV_KEY_ON_A_BOX)
    return Result("api keys", False, A_DEV_KEY, advisory=True)


# The order the report reads, and the first ✗ in it is the one the verdict names. `lk` is last
# because it is the only line that cannot make the verdict.
CHECKS: tuple[Check, ...] = (
    check_which_keys_are_honoured,
    check_provider_keys,
    check_provider_keys_answer,
    check_livekit_is_reachable,
    check_postgres_is_ready,
    check_the_embedder_answers,
    check_the_livekit_cli_is_installed,
)
ONLY_ON_A_HUB: frozenset[Check] = frozenset({check_postgres_is_ready, check_the_embedder_answers})


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
