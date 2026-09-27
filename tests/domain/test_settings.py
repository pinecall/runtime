"""Tests for Settings: the three sources, their precedence, empty values and startup checks."""

import os
from pathlib import Path

import pytest

from pinecall.domain.errors import SettingsRefused
from pinecall.domain.settings import Settings, env_files, load

A_FAKE_KEY = "fake-key-written-by-this-test"

# Libraries get values by parameter, never via os.environ, so Settings stays the only source.
THE_WAYS_TO_WRITE_ONE = ("os.environ[", "os.environ.setdefault", "os.putenv", "environ.update")


# Every test starts in an empty directory with none of our variables exported: a developer's own
# .env and shell must not reach the suite.
@pytest.fixture(autouse=True)
def a_clean_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("PINECALL_", "LIVEKIT_", "DATABASE_", "CREDENTIALS_DIRECTORY")):
            monkeypatch.delenv(name)
    monkeypatch.delenv("ELEVEN_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


def write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_a_key_in_the_env_file_of_the_working_directory_is_read(tmp_path: Path) -> None:
    write(tmp_path / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    assert load().variables["ELEVEN_API_KEY"] == A_FAKE_KEY


def test_the_runtime_env_file_is_read_when_the_process_starts_at_the_repo_root(
    tmp_path: Path,
) -> None:
    write(tmp_path / "runtime" / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    assert load().variables["ELEVEN_API_KEY"] == A_FAKE_KEY


def test_a_real_environment_variable_beats_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\nPINECALL_FLEET=from-the-file\n")
    monkeypatch.setenv("ELEVEN_API_KEY", "exported-and-therefore-the-winner")
    monkeypatch.setenv("PINECALL_FLEET", "pinecall-sandbox")
    settings = load()
    assert settings.variables["ELEVEN_API_KEY"] == "exported-and-therefore-the-winner"
    assert settings.fleet == "pinecall-sandbox"


def test_a_name_the_runtime_does_not_read_is_kept_for_the_catalog_and_never_an_error(
    tmp_path: Path,
) -> None:
    write(tmp_path / ".env", f"SOME_OTHER_PROJECT=nothing\nELEVEN_API_KEY={A_FAKE_KEY}\n")
    settings = load()
    assert settings.variables["ELEVEN_API_KEY"] == A_FAKE_KEY
    assert settings.variables["SOME_OTHER_PROJECT"] == "nothing"
    assert "SOME_OTHER_PROJECT" not in Settings.model_fields


def test_the_file_is_found_from_a_directory_deeper_in_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path / ".git", "gitdir: elsewhere\n")
    write(tmp_path / "runtime" / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    tenant = tmp_path / "examples" / "clinica-norte"
    tenant.mkdir(parents=True)
    monkeypatch.chdir(tenant)
    assert env_files() == [tmp_path / "runtime" / ".env"]
    assert load().variables["ELEVEN_API_KEY"] == A_FAKE_KEY


def test_the_walk_stops_at_the_repository_root_and_never_reads_a_parents_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    repository = tmp_path / "a-project-of-somebody-elses"
    write(repository / ".git", "gitdir: elsewhere\n")
    monkeypatch.chdir(repository)
    assert env_files() == []
    assert "ELEVEN_API_KEY" not in load().variables


def test_the_files_read_are_named_in_the_order_the_later_wins(tmp_path: Path) -> None:
    write(tmp_path / ".env", "PINECALL_FLEET=first\n")
    write(tmp_path / "runtime" / ".env", "PINECALL_FLEET=second\n")
    assert env_files() == [tmp_path / ".env", tmp_path / "runtime" / ".env"]
    assert load().fleet == "second"


def test_no_env_file_at_all_leaves_the_settings_to_the_environment() -> None:
    assert env_files() == []
    assert load().tei_url == "http://127.0.0.1:8081"


def test_a_credentials_directory_is_read_by_the_environments_own_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "creds"
    write(store / "PINECALL_OPS_KEY", "ops-from-a-credential\n")
    write(store / "LIVEKIT_API_KEY", "APIcredential")
    write(store / "DATABASE_URL", "postgresql://u:p@h/db")
    write(store / "ANTHROPIC_API_KEY", "from-a-credential")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(store))
    settings = load()
    assert settings.ops_key == "ops-from-a-credential"
    assert settings.livekit_api_key == "APIcredential"
    assert settings.database_url == "postgresql://u:p@h/db"
    assert settings.variables["ANTHROPIC_API_KEY"] == "from-a-credential"


def test_the_environment_wins_over_the_file_and_the_file_over_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "creds"
    write(store / "PINECALL_OPS_KEY", "from-the-credential")
    write(store / "PINECALL_FLEET", "from-the-credential")
    write(tmp_path / ".env", "PINECALL_FLEET=from-the-file\n")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(store))
    monkeypatch.setenv("PINECALL_OPS_KEY", "from-the-environment")
    settings = load()
    assert settings.ops_key == "from-the-environment"
    assert settings.fleet == "from-the-file"


def test_a_field_reads_its_own_variable_name_and_our_knobs_carry_the_prefix() -> None:
    assert Settings.model_fields["livekit_url"].alias == "LIVEKIT_URL"
    assert Settings.model_fields["worker_key"].alias == "PINECALL_WORKER_KEY"
    assert all(
        field.alias is None or field.alias.isupper() or "_" in field.alias
        for field in Settings.model_fields.values()
    )


def test_a_bare_name_in_the_file_means_the_knob_is_unset(tmp_path: Path) -> None:
    write(tmp_path / ".env", "PINECALL_MAX_JOBS=\nPINECALL_WORKER_NAME=\nELEVEN_API_KEY=\n")
    settings = load()
    assert settings.max_jobs is None
    assert settings.worker_name is None
    assert "ELEVEN_API_KEY" not in settings.variables


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a file whatever its mode says")
def test_an_env_file_that_cannot_be_opened_is_a_sentence_naming_it(tmp_path: Path) -> None:
    write(tmp_path / ".env", "ELEVEN_API_KEY=never-read\n")
    (tmp_path / ".env").chmod(0o000)
    try:
        with pytest.raises(SettingsRefused) as refused:
            load()
    finally:
        (tmp_path / ".env").chmod(0o600)
    assert ".env" in str(refused.value)
    assert "never skipped in silence" in str(refused.value)


def test_a_worker_unit_joins_the_fleet_pinecall_unless_it_names_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load().fleet == "pinecall"
    monkeypatch.setenv("PINECALL_FLEET", "pinecall-sandbox")
    assert load().fleet == "pinecall-sandbox"


# Fleet names end up in Twilio usernames and SFU trunk names; empty would match every room.
@pytest.mark.parametrize("spelled", ["Pine call", "pinecall:sandbox", "-sandbox"])
def test_a_fleet_spelled_unlike_a_slug_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, spelled: str
) -> None:
    monkeypatch.setenv("PINECALL_FLEET", spelled)
    with pytest.raises(SettingsRefused, match="PINECALL_FLEET"):
        load()


def test_a_zone_nobody_has_heard_of_is_refused_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINECALL_TIMEZONE", "Mars/Olympus")
    with pytest.raises(SettingsRefused, match="IANA"):
        load()
    monkeypatch.setenv("PINECALL_TIMEZONE", "Europe/Madrid")
    assert load().timezone == "Europe/Madrid"


def test_a_number_a_word_and_a_switch_are_read_as_what_they_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_MAX_JOBS", "4")
    monkeypatch.setenv("PINECALL_SIGNUP", "1")
    monkeypatch.setenv("PINECALL_ROLE", "worker")
    settings = load()
    assert (settings.max_jobs, settings.signup, settings.role) == (4, True, "worker")
    monkeypatch.setenv("PINECALL_ROLE", "janitor")
    with pytest.raises(SettingsRefused, match="PINECALL_ROLE"):
        load()


def test_what_settings_print_never_shows_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINECALL_OPS_KEY", "the-ops-key-of-this-box")
    monkeypatch.setenv("PINECALL_VAULT_KEY", "the-vault-key-of-this-box")
    monkeypatch.setenv("ELEVEN_API_KEY", A_FAKE_KEY)
    shown = repr(load())
    assert "the-ops-key-of-this-box" not in shown
    assert "the-vault-key-of-this-box" not in shown
    assert A_FAKE_KEY not in shown
    assert "fleet='pinecall'" in shown


def test_nothing_in_the_runtime_writes_into_the_environment() -> None:
    package = Path(__file__).resolve().parents[2] / "pinecall"
    offenders = [
        str(module)
        for module in package.rglob("*.py")
        if any(written in module.read_text("utf-8") for written in THE_WAYS_TO_WRITE_ONE)
    ]
    assert not offenders, f"these modules write an environment variable: {offenders}"
