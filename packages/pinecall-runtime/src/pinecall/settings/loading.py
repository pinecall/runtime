"""The settings of this process now, and which environment variable each field reads."""

from pinecall.settings.env_files import env_file_refusal
from pinecall.settings.schema import ENV_PREFIX, Settings


def load_settings() -> Settings:
    """The environment now. No hidden global; an unopenable .env is a sentence, not a trace."""
    try:
        return Settings()
    except OSError as failed:
        raise env_file_refusal(failed) from failed


def variable_of(field: str) -> str:
    """The environment variable one settings field reads: its own alias, or PINECALL_ + its name."""
    alias = Settings.model_fields[field].validation_alias
    return alias if isinstance(alias, str) else f"{ENV_PREFIX}{field.upper()}"
