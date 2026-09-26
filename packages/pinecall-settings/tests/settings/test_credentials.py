"""A box hands its secrets over as systemd credentials: one file per name, read as the env is."""

from pathlib import Path

import pytest

from pinecall.settings import Settings

pytestmark = pytest.mark.unit


# pydantic-settings takes its per-instance knobs as underscored keyword arguments it does not
# type, so the one call that uses them is here, and the one ignore with it.
def read(secrets: Path) -> Settings:
    """Settings with this directory as its credentials and no dotenv file at all."""
    return Settings(_secrets_dir=secrets, _env_file=None)  # pyright: ignore[reportCallIssue]


# The names are the environment's — the alias where the field declares one, the PINECALL_ prefix
# where it does not — so `/run/credentials/<unit>/DATABASE_URL` is DATABASE_URL and a unit's
# `ImportCredential=` lines read like its EnvironmentFile did. Measured on 2026-09-09, then pinned.
def test_a_credentials_directory_is_read_by_the_environments_own_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The suite runs on dead-sentinel keys in the environment, and the environment wins by design.
    for name in ("PINECALL_OPS_KEY", "LIVEKIT_API_KEY", "DATABASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "PINECALL_OPS_KEY").write_text("ops-from-a-credential\n")
    (tmp_path / "LIVEKIT_API_KEY").write_text("APIcredential")
    (tmp_path / "DATABASE_URL").write_text("postgresql://u:p@h/db")
    (tmp_path / "ANTHROPIC_API_KEY").write_text("sk-ant-from-a-credential")

    settings = read(tmp_path)

    assert settings.ops_key == "ops-from-a-credential"
    assert settings.livekit_api_key == "APIcredential"
    assert settings.database_url == "postgresql://u:p@h/db"
    assert settings.anthropic_api_key == "sk-ant-from-a-credential"


def test_a_real_environment_variable_wins_over_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "PINECALL_OPS_KEY").write_text("from-the-file")
    monkeypatch.setenv("PINECALL_OPS_KEY", "from-the-environment")

    assert read(tmp_path).ops_key == "from-the-environment"
