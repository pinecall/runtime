"""The .env the runtime reads: which file, who wins, and what a name it does not know costs."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from pinecall.settings import NOBODY_TO_ASK, NobodyToAsk, Settings, load_settings, variable_of
from pinecall.settings.env_files import ENV_FILES, EnvFileRefused, env_files_read
from tests.support.tree import PACKAGE_ROOT, ROOT

pytestmark = pytest.mark.unit

# Never a real key, and never a real value: every test here writes its own file with this.
A_FAKE_KEY = "fake-key-written-by-this-test"

# Every way a module could set a variable behind Settings' back.
THE_WAYS_TO_WRITE_ONE = ("os.environ[", "os.environ.setdefault", "os.putenv", "environ.update")


def write_env_file(path: Path, body: str) -> None:
    """A .env as an operator types one, in a directory the test owns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# tests/conftest.py turns the dotenv source off for the whole session, so a developer with real
# keys in runtime/.env runs the suite CI runs. These tests are ABOUT that source, so they are the
# ones that turn it back on — for themselves, over a file they wrote in a directory they own.
@pytest.fixture
def the_env_file_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """This test asks for the mechanism the suite switches off, and only for its own duration."""
    monkeypatch.setitem(Settings.model_config, "env_file", ENV_FILES)


@pytest.mark.usefixtures("the_env_file_is_read")
def test_a_key_in_the_env_file_of_the_working_directory_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_env_file(tmp_path / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    monkeypatch.delenv("ELEVEN_API_KEY")
    monkeypatch.chdir(tmp_path)
    assert load_settings().eleven_api_key == A_FAKE_KEY


@pytest.mark.usefixtures("the_env_file_is_read")
def test_the_runtime_env_file_is_read_when_the_process_starts_at_the_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_env_file(tmp_path / "runtime" / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    monkeypatch.delenv("ELEVEN_API_KEY")
    monkeypatch.chdir(tmp_path)
    assert load_settings().eleven_api_key == A_FAKE_KEY


@pytest.mark.usefixtures("the_env_file_is_read")
def test_a_real_environment_variable_beats_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_env_file(tmp_path / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    monkeypatch.setenv("ELEVEN_API_KEY", "exported-and-therefore-the-winner")
    monkeypatch.chdir(tmp_path)
    assert load_settings().eleven_api_key == "exported-and-therefore-the-winner"


@pytest.mark.usefixtures("the_env_file_is_read")
def test_a_name_the_runtime_does_not_read_is_ignored_and_never_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file a laptop already has carries v1's names; the runtime skips them and starts."""
    write_env_file(
        tmp_path / ".env",
        f"PINECALL_PROJECT_ID=v1-only\nSOME_OTHER_PROJECT=nothing\nELEVEN_API_KEY={A_FAKE_KEY}\n",
    )
    monkeypatch.delenv("ELEVEN_API_KEY")
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.eleven_api_key == A_FAKE_KEY
    assert not hasattr(settings, "project_id")


@pytest.mark.usefixtures("the_env_file_is_read")
def test_the_file_is_found_from_a_directory_deeper_in_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An app started from an example directory deeper in the checkout finds the runtime's file."""
    write_env_file(tmp_path / ".git", "gitdir: elsewhere\n")
    write_env_file(tmp_path / "runtime" / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    tenant = tmp_path / "examples" / "clinica-norte"
    tenant.mkdir(parents=True)
    monkeypatch.delenv("ELEVEN_API_KEY")
    monkeypatch.chdir(tenant)
    assert env_files_read() == [tmp_path / "runtime" / ".env"]
    assert load_settings().eleven_api_key == A_FAKE_KEY


@pytest.mark.usefixtures("the_env_file_is_read")
def test_the_walk_stops_at_the_repository_root_and_never_reads_a_parents_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray .env above the project is the wrong keys, silently — worse than finding none."""
    write_env_file(tmp_path / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    repository = tmp_path / "a-project-of-somebody-elses"
    write_env_file(repository / ".git", "gitdir: elsewhere\n")
    monkeypatch.delenv("ELEVEN_API_KEY")
    monkeypatch.chdir(repository)
    assert env_files_read() == []
    assert load_settings().eleven_api_key is None


def test_the_suite_itself_never_reads_an_env_file_lying_in_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The invariant: a developer with real keys on disk gets the suite CI gets, key for key."""
    write_env_file(tmp_path / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    write_env_file(tmp_path / "runtime" / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    monkeypatch.delenv("ELEVEN_API_KEY")
    monkeypatch.chdir(tmp_path)
    assert load_settings().eleven_api_key is None


def test_the_suite_reads_no_env_file_the_walk_would_now_find_up_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk widened what a process can find; conftest still closes the source for the suite."""
    write_env_file(tmp_path / "runtime" / ".env", f"ELEVEN_API_KEY={A_FAKE_KEY}\n")
    deeper = tmp_path / "examples" / "clinica-norte"
    deeper.mkdir(parents=True)
    monkeypatch.delenv("ELEVEN_API_KEY")
    monkeypatch.chdir(deeper)
    assert env_files_read() == [tmp_path / "runtime" / ".env"]
    assert load_settings().eleven_api_key is None


def test_the_files_read_are_named_in_the_order_pydantic_reads_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_env_file(tmp_path / ".env", "")
    write_env_file(tmp_path / "runtime" / ".env", "")
    monkeypatch.chdir(tmp_path)
    assert env_files_read() == [tmp_path / ".env", tmp_path / "runtime" / ".env"]


def test_no_env_file_at_all_leaves_the_settings_to_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert env_files_read() == []
    assert load_settings().tei_url == "http://127.0.0.1:8081"


def test_a_field_with_no_alias_reads_its_name_under_the_prefix() -> None:
    assert variable_of("eleven_api_key") == "ELEVEN_API_KEY"
    assert variable_of("worker_key") == "PINECALL_WORKER_KEY"


# The other half of the rule the file above states: Settings READS the environment, and nothing
# writes it. Handing a library its values through os.environ works once and hides the source for
# good — livekit's url and key pair reach it by parameter (worker/main.py) for exactly this reason.
def test_nothing_in_the_runtime_writes_into_the_environment() -> None:
    offenders = [
        str(path.relative_to(PACKAGE_ROOT))
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        if any(written in path.read_text(encoding="utf-8") for written in THE_WAYS_TO_WRITE_ONE)
    ]
    assert not offenders, f"these modules write an environment variable: {offenders}"


# `cp .env.example .env` is the first step of docs/from-zero.md, and .env.example writes every
# optional knob as a bare `NAME=`. For a string that already meant "unset"; for the one `int | None`
# it meant the process would not start at all — `max_jobs · Input should be a valid integer`, on
# EVERY verb, from a file the walkthrough told the reader to make.
@pytest.mark.usefixtures("the_env_file_is_read")
def test_a_bare_name_in_the_file_means_the_knob_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_env_file(tmp_path / ".env", "PINECALL_MAX_JOBS=\nPINECALL_WORKER_NAME=\n")
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.max_jobs is None
    assert settings.worker_name is None


@pytest.mark.usefixtures("the_env_file_is_read")
def test_the_example_file_copied_verbatim_builds_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cp .env.example .env` and nothing else: the exact state a reader is told to be in."""
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    write_env_file(tmp_path / ".env", example)
    monkeypatch.chdir(tmp_path)
    assert load_settings().max_jobs is None


# A .env this process cannot open used to arrive as a PermissionError traceback out of the middle
# of python-dotenv — a person running a verb as the service user inside another user's home, on
# the box (2026-09-20). It is not read, and it is never skipped in silence either.
@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a file whatever its mode says")
@pytest.mark.usefixtures("the_env_file_is_read")
def test_an_env_file_that_cannot_be_opened_is_a_sentence_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_env_file(tmp_path / ".env", "ELEVEN_API_KEY=never-read\n")
    (tmp_path / ".env").chmod(0o000)
    monkeypatch.chdir(tmp_path)
    try:
        with pytest.raises(EnvFileRefused) as refused:
            load_settings()
    finally:
        (tmp_path / ".env").chmod(0o600)
    assert ".env" in str(refused.value)
    assert "never skipped in silence" in str(refused.value)


# A box that runs one instance is production, as every box was before there were two: the sandbox
# is said on purpose, by its own instance's environment file.
def test_a_process_that_never_said_its_world_is_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PINECALL_WORLD", raising=False)
    assert load_settings().world == "production"


# A sandbox holds no password and mints no person of its own: with nobody to ask it could let
# nobody in, and it is refused before it runs anything rather than at the first sign-in.
def test_a_sandbox_with_nobody_to_ask_who_a_person_is_does_not_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_WORLD", "sandbox")
    monkeypatch.delenv("PINECALL_IDENTITY_URL", raising=False)
    with pytest.raises(NobodyToAsk) as refused:
        load_settings()
    assert str(refused.value) == NOBODY_TO_ASK


def test_the_world_is_read_and_the_fleet_is_pinecall_unless_the_instance_names_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_WORLD", "sandbox")
    monkeypatch.setenv("PINECALL_IDENTITY_URL", "https://box.example.test")
    assert (load_settings().world, load_settings().fleet) == ("sandbox", "pinecall")
    monkeypatch.setenv("PINECALL_FLEET", "pinecall-sandbox")
    assert load_settings().fleet == "pinecall-sandbox"


# The name ends up in a Twilio credential username and an SFU trunk name, and an empty one would
# answer every room on the deployment: a process that spelled it wrong never starts.
@pytest.mark.parametrize("spelled", ["", "Pine call", "pinecall:sandbox", "-sandbox"])
def test_a_fleet_spelled_unlike_a_slug_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, spelled: str
) -> None:
    monkeypatch.setenv("PINECALL_FLEET", spelled)
    with pytest.raises(ValidationError, match="fleet"):
        load_settings()
