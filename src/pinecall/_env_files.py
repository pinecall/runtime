"""Which .env files a process reads: two names, looked for from the working directory upwards."""

from collections.abc import Iterator
from pathlib import Path

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
