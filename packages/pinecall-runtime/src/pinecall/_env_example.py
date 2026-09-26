""".env.example, rendered from Settings' declared aliases so the file and the class cannot drift."""

from pathlib import Path

from pydantic.fields import FieldInfo

from pinecall._env_files import ENV_FILES
from pinecall._settings import Settings, variable_of

HEADER = f"""\
# The only .env ever committed, and it is GENERATED: `scripts/generate-env-example` renders it
# from the aliases src/pinecall/_settings.py declares, and a test fails when the two drift.
# Copy it to {ENV_FILES[0]} and fill what you have. A real environment variable wins over the file,
# and a name this runtime does not read is ignored, never an error.
"""


def render_env_example() -> str:
    """Every declared alias, its default, and the one line the class says about it."""
    blocks = [
        f"# {field.description}\n{variable_of(name)}={_default_written_out(field)}"
        for name, field in Settings.model_fields.items()
    ]
    return HEADER + "\n" + "\n\n".join(blocks) + "\n"


def write_env_example(path: Path) -> None:
    """What `scripts/generate-env-example` calls. Idempotent: a second run changes nothing."""
    path.write_text(render_env_example(), encoding="utf-8")


def _default_written_out(field: FieldInfo) -> str:
    """A default as an operator types it: nothing for a key nobody set, true/false for a flag."""
    if field.default is None:
        return ""
    if isinstance(field.default, bool):
        return "true" if field.default else "false"
    return str(field.default)
