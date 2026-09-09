"""The doctor against fakes: every check gives a reason, and the first ✗ decides the exit code."""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from pinecall._settings import load_settings
from pinecall.cli import main
from pinecall.cli.doctor import verbs as doctor
from pinecall.cli.doctor.probes import Probes

pytestmark = pytest.mark.unit


def probes_that_answer(
    *,
    http_status: Callable[[str], int] = lambda _url: 200,
    knock: Callable[[str, Mapping[str, str]], int] = lambda _url, _headers: 200,
    postgres_extensions: Callable[[str], set[str]] = lambda _dsn: set(doctor.REQUIRED_EXTENSIONS),
    executable_path: Callable[[str], str | None] = lambda program: f"/opt/homebrew/bin/{program}",
) -> Probes:
    """A stack where everything is up, with one answer swapped for the check under test."""
    return Probes(
        http_status=http_status,
        knock=knock,
        postgres_extensions=postgres_extensions,
        executable_path=executable_path,
    )


def elevenlabs_refuses(url: str, _headers: Mapping[str, str]) -> int:
    """One vendor of the five says the key is dead; the other four answer."""
    return 401 if "elevenlabs" in url else 200


def refuse_http(_url: str) -> int:
    """What httpx raises when nothing listens on the port."""
    raise ConnectionRefusedError("[Errno 61] Connection refused")


def tei_that_serves_nothing(url: str) -> int:
    """TEI's port answers, but /info does not: the embedder never finished loading a model."""
    return 404 if url.endswith("/info") else 200


def test_a_stack_that_is_all_up_reports_all_up_and_nothing_down() -> None:
    results = doctor.run_checks(load_settings(), probes_that_answer())
    assert all(result.ok for result in results)
    assert doctor.first_failure(results) is None
    assert "all up" in doctor.render_report(results)


def test_a_livekit_that_refuses_the_connection_is_the_first_thing_down() -> None:
    results = doctor.run_checks(load_settings(), probes_that_answer(http_status=refuse_http))
    down = doctor.first_failure(results)
    assert down is not None
    assert down.name == "livekit"
    assert "Connection refused" in down.detail
    assert "first down: livekit" in doctor.render_report(results)


def test_the_livekit_websocket_url_is_asked_over_http() -> None:
    asked: list[str] = []

    def record(url: str) -> int:
        asked.append(url)
        return 200

    doctor.run_checks(load_settings(), probes_that_answer(http_status=record))
    assert asked[0] == "http://127.0.0.1:1/"


def test_a_postgres_without_the_search_extension_names_the_one_that_is_missing() -> None:
    results = doctor.run_checks(
        load_settings(),
        probes_that_answer(postgres_extensions=lambda _dsn: {"vector"}),
    )
    down = doctor.first_failure(results)
    assert down is not None
    assert down.name == "postgres"
    assert "pg_textsearch" in down.detail


def test_the_report_never_prints_the_postgres_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://pinecall:s3cret@127.0.0.1:5432/pinecall")
    report = doctor.render_report(doctor.run_checks(load_settings(), probes_that_answer()))
    assert "s3cret" not in report
    assert "pinecall@127.0.0.1:5432/pinecall" in report


def test_an_ipv6_database_host_keeps_the_brackets_that_make_it_an_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://pinecall:s3cret@[::1]:5432/pinecall")
    report = doctor.render_report(doctor.run_checks(load_settings(), probes_that_answer()))
    assert "postgresql://pinecall@[::1]:5432/pinecall" in report


def test_a_tei_that_answers_anything_but_200_is_reported_and_stops_no_call() -> None:
    """Nothing in the tree embeds yet: the ✗ is printed, with why, and never makes the verdict."""
    results = doctor.run_checks(
        load_settings(),
        probes_that_answer(http_status=tei_that_serves_nothing),
    )
    tei = next(result for result in results if result.name == "tei")
    assert not tei.ok
    assert tei.advisory
    assert "404" in tei.detail
    assert "stops no call" in tei.detail
    assert doctor.first_failure(results) is None


def test_a_provider_key_is_reported_by_its_variable_and_never_by_its_value() -> None:
    keys = doctor.run_checks(load_settings(), probes_that_answer())[0]
    assert keys.name == "provider keys"
    assert keys.ok
    assert "ANTHROPIC_API_KEY" in keys.detail
    assert "sk-ant-dead-sentinel" not in keys.detail


def test_a_role_with_no_key_at_all_names_the_role_and_what_to_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ELEVEN_API_KEY")
    down = doctor.first_failure(doctor.run_checks(load_settings(), probes_that_answer()))
    assert down is not None
    assert down.name == "provider keys"
    assert "tts" in down.detail
    assert "ELEVEN_API_KEY" in down.detail


def test_the_doctor_exits_zero_with_everything_up(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(doctor, "live_probes", probes_that_answer)
    assert main(["doctor"]) == 0
    printed = capsys.readouterr().out
    assert printed.count("✓") == len(doctor.CHECKS)
    assert "all up" in printed


def test_the_doctor_exits_one_naming_the_first_thing_down(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        doctor,
        "live_probes",
        lambda: probes_that_answer(postgres_extensions=lambda _dsn: set()),
    )
    assert main(["doctor"]) == 1
    printed = capsys.readouterr().out
    assert "✗ postgres" in printed
    assert "first down: postgres" in printed


def test_a_key_every_vendor_answers_is_reported_by_its_variable_alone() -> None:
    answer = doctor.run_checks(load_settings(), probes_that_answer())[1]
    assert answer.name == "provider keys answer"
    assert answer.ok
    assert "ELEVEN_API_KEY" in answer.detail
    assert "dead-sentinel" not in answer.detail


def test_a_refused_key_is_the_first_thing_down_and_says_where_a_live_one_goes() -> None:
    """2026-09-09: a dead ElevenLabs key sat in the credstore until a caller heard the silence."""
    results = doctor.run_checks(load_settings(), probes_that_answer(knock=elevenlabs_refuses))
    down = doctor.first_failure(results)
    assert down is not None
    assert down.name == "provider keys answer"
    assert "refused ELEVEN_API_KEY (HTTP 401)" in down.detail
    assert "make secret NAME=ELEVEN_API_KEY" in down.detail
    assert "ANTHROPIC_API_KEY" not in down.detail


def test_the_knock_carries_the_key_where_the_vendor_reads_it() -> None:
    knocked: dict[str, Mapping[str, str]] = {}

    def record(url: str, headers: Mapping[str, str]) -> int:
        knocked[url] = headers
        return 200

    doctor.run_checks(load_settings(), probes_that_answer(knock=record))
    assert knocked["https://api.elevenlabs.io/v1/user"]["xi-api-key"].endswith("dead-sentinel")
    assert knocked["https://api.anthropic.com/v1/models"]["anthropic-version"] == "2023-06-01"
    assert knocked["https://api.openai.com/v1/models"]["authorization"].startswith("Bearer ")


def test_a_vendor_that_cannot_be_reached_is_named_with_the_reason() -> None:
    def unreachable(_url: str, _headers: Mapping[str, str]) -> int:
        raise TimeoutError("timed out")

    down = doctor.first_failure(
        doctor.run_checks(load_settings(), probes_that_answer(knock=unreachable))
    )
    assert down is not None
    assert down.name == "provider keys answer"
    assert "SONIOX_API_KEY unreachable — TimeoutError: timed out" in down.detail


def test_a_worker_is_asked_after_no_postgres_and_no_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker box has neither, and a deploy that stopped on the hub's ✗ would never finish."""
    monkeypatch.setenv("PINECALL_ROLE", "worker")
    results = doctor.run_checks(
        load_settings(),
        probes_that_answer(postgres_extensions=lambda _dsn: set()),
    )
    assert [result.name for result in results] == [
        "provider keys",
        "provider keys answer",
        "livekit",
        "lk",
    ]
    assert doctor.first_failure(results) is None


def test_a_hub_is_asked_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINECALL_ROLE", "hub")
    results = doctor.run_checks(load_settings(), probes_that_answer())
    assert len(results) == len(doctor.CHECKS)


def test_the_report_opens_with_the_env_file_it_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The trap ms-4 documented: a .env the runtime ignores can never be silent again."""
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / ".env").write_text("PINECALL_LOG_LEVEL=INFO\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(doctor, "live_probes", probes_that_answer)
    assert main(["doctor"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == f"env: {tmp_path / 'runtime' / '.env'}"


def test_the_report_says_environment_only_when_there_is_no_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(doctor, "live_probes", probes_that_answer)
    assert main(["doctor"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "env: no .env — environment only"


def test_bench_says_no_embedder_is_wired_yet(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(doctor, "live_probes", probes_that_answer)
    assert main(["doctor", "--bench"]) == 0
    assert "no embedder wired yet" in capsys.readouterr().out


def test_the_livekit_cli_is_reported_with_the_path_it_was_found_at() -> None:
    lk = doctor.run_checks(load_settings(), probes_that_answer())[-1]
    assert lk.name == "lk"
    assert lk.ok
    assert "/opt/homebrew/bin/lk" in lk.detail
    assert "lk docs" in lk.detail


def test_a_machine_without_the_livekit_cli_is_told_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """It reads documentation and manages trunks; it carries no call, so it never fails a box."""
    monkeypatch.setattr(
        doctor,
        "live_probes",
        lambda: probes_that_answer(executable_path=lambda _program: None),
    )
    assert main(["doctor"]) == 0
    printed = capsys.readouterr().out
    assert "! lk" in printed
    assert "brew install livekit-cli" in printed
    assert "all up" in printed


def test_the_doctor_never_runs_the_livekit_cli_it_only_looks_for_it() -> None:
    asked: list[str] = []

    def record(program: str) -> str | None:
        asked.append(program)
        return "/usr/local/bin/lk"

    doctor.run_checks(load_settings(), probes_that_answer(executable_path=record))
    assert asked == ["lk"]
