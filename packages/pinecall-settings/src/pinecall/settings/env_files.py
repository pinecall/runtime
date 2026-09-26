"""Which .env files a process reads: two names, looked for from the working directory upwards."""

from collections.abc import Iterator
from pathlib import Path

from pinecall.errors import PinecallError

# The two names a .env is looked for under: in the directory the process started in, then in
# each parent up to the repository root. `uv run pinecall-runtime …` starts in the runtime
# directory, where the first name is the file; an app started from a checkout's root, or from an
# example directory deeper in it, finds the same file under the second. Where the walk stops with
# both names present, the later wins — pydantic-settings' own order for a list of files.
ENV_FILES: tuple[str, ...] = (".env", "runtime/.env")


def env_files_read() -> list[Path]:
    """The .env files a Settings built here reads, in the order pydantic reads them."""
    for folder in _folders_up_to_the_repository_root(Path.cwd()):
        found = [folder / name for name in ENV_FILES if (folder / name).is_file()]
        if found:
            return found
    return []


# The walk is BOUNDED on purpose: a stray .env in a directory above the project would be the wrong
# keys, silently, which is a worse failure than finding none. docs/decisions/settings.md says why.
def _folders_up_to_the_repository_root(start: Path) -> Iterator[Path]:
    """`start`, then each parent, stopping at the first one holding a `.git` — never above it."""
    folder = start.resolve()
    for candidate in (folder, *folder.parents):
        yield candidate
        if (candidate / ".git").exists():
            return


# A `.env` that is there and cannot be opened is not the same as no `.env`: the file may hold the
# very key the verb needs, and reading none of it silently is how a process runs against the wrong
# database. python-dotenv opens it while pydantic builds its source, so what a person saw was a
# PermissionError out of the middle of a library (`sudo -u pinecall` inside another user's home,
# on the box, 2026-09-20). One sentence, naming the file and why.
UNREADABLE_ENV = "cannot read {file}: {why} — a .env that is there is never skipped in silence"


class EnvFileRefused(PinecallError):
    """A .env this process cannot open. Nothing is read from it, and nothing pretends otherwise."""


def env_file_refusal(failed: OSError) -> EnvFileRefused:
    """The OS's own complaint about a .env, as the sentence a person reads."""
    return EnvFileRefused(
        UNREADABLE_ENV.format(file=failed.filename or ENV_FILES[0], why=failed.strerror or failed)
    )
