"""The doctor against fakes: every check gives a reason, and the first ✗ decides the exit code."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from pinecall._settings import load_settings
from pinecall.cli import main
from pinecall.cli.doctor import verbs as doctor
from pinecall.cli.doctor.probes import Probes
from pinecall.mail import BoxMail
from pinecall.orgs.mail import KeptMail
from pinecall.types import Mailbox
from tests.cli.doctor.reading import a_box_that_posts_mail, named, probes_that_answer

pytestmark = pytest.mark.unit


def elevenlabs_refuses(url: str, _headers: Mapping[str, str]) -> int:
    """One vendor of the five says the key is dead; the other four answer."""
    return 401 if "elevenlabs" in url else 200


def refuse_http(_url: str) -> int:
    """What httpx raises when nothing listens on the port."""
    raise ConnectionRefusedError("[Errno 61] Connection refused")


def tei_that_serves_nothing(url: str) -> int:
    """TEI's port answers, but /info does not: the embedder never finished loading a model."""
    return 404 if url.endswith("/info") else 200


def test_a_stack_that_is_all_up_reports_all_up_and_nothing_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a_box_that_posts_mail(monkeypatch)
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
    """A down embedder is printed, with why, and never makes the verdict: no call needs one."""
    embedder = _the_embedder(probes_that_answer(http_status=tei_that_serves_nothing))
    assert not embedder.ok
    assert embedder.advisory
    assert "404" in embedder.detail
    assert "stops no call" in embedder.detail
    assert (
        doctor.first_failure(_the_report(probes_that_answer(http_status=tei_that_serves_nothing)))
        is None
    )


def test_a_hub_whose_embedder_is_down_is_the_verdict_and_is_told_to_start_the_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hub answers the knowledge pushes: a shut door there is an outage the operator can fix."""
    monkeypatch.setenv("PINECALL_ROLE", "hub")
    results = _the_report(probes_that_answer(http_status=tei_that_serves_nothing))
    down = doctor.first_failure(results)
    assert down is not None
    assert down.name == "embedder"
    assert not down.advisory
    assert "a knowledge push answers 503" in down.detail
    assert "systemctl start pinecall-tei" in down.detail
    assert "EMBED_PROVIDER" in down.detail


def test_a_hub_that_embeds_through_a_vendor_is_told_which_key_to_bring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix is a credential and not a container, so the sentence names the credential."""
    monkeypatch.setenv("PINECALL_ROLE", "hub")
    monkeypatch.setenv("EMBED_PROVIDER", "perplexity")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "")
    embedder = _the_embedder(probes_that_answer())
    assert not embedder.ok
    assert not embedder.advisory
    assert "no PERPLEXITY_API_KEY" in embedder.detail
    assert "make secret NAME=PERPLEXITY_API_KEY" in embedder.detail
    assert "systemctl" not in embedder.detail


def test_the_embedder_line_says_which_provider_and_model_this_box_embeds_with() -> None:
    embedder = _the_embedder(probes_that_answer())
    assert embedder.ok
    assert "tei · BAAI/bge-m3" in embedder.detail
    assert "http://127.0.0.1:8081/info" in embedder.detail


def test_a_hosted_embedder_with_no_key_is_named_by_its_variable_and_never_knocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key is missing, so nothing is asked of the vendor: the fix is a variable, not a probe."""
    monkeypatch.setenv("EMBED_PROVIDER", "perplexity")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "")
    embedder = _the_embedder(probes_that_answer())
    assert not embedder.ok
    assert embedder.advisory
    assert "perplexity · pplx-embed-context-v1-0.6b" in embedder.detail
    assert "no PERPLEXITY_API_KEY" in embedder.detail


def test_a_hosted_embedder_that_answers_at_all_is_up_whatever_status_it_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api.perplexity.ai has no /info and no free door: that it answers a GET is the whole check."""
    monkeypatch.setenv("EMBED_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dead-sentinel")
    embedder = _the_embedder(probes_that_answer(http_status=lambda _url: 405))
    assert embedder.ok
    assert "openrouter · perplexity/pplx-embed-v1-0.6b" in embedder.detail
    assert "https://openrouter.ai/api/v1" in embedder.detail


def _the_embedder(probes: Probes) -> doctor.Result:
    """The one line of the report this box's embedder gets, whichever provider it names."""
    return next(result for result in _the_report(probes) if result.name == "embedder")


def _the_report(probes: Probes) -> list[doctor.Result]:
    """Every check, against a stack where only what a test swapped is down."""
    return doctor.run_checks(load_settings(), probes)


def test_a_provider_key_is_reported_by_its_variable_and_never_by_its_value() -> None:
    keys = named("provider keys", doctor.run_checks(load_settings(), probes_that_answer()))
    assert keys.ok
    assert "ANTHROPIC_API_KEY" in keys.detail
    assert "sk-ant-dead-sentinel" not in keys.detail


# A role is empty only when NO catalogued vendor of that role has a key, which is forty-odd
# variables now — so the test empties the ones ring 0 sets rather than the one it used to. What
# the line then says is the ONE vendor worth naming to somebody who has no key at all
# (doctor/verbs.py, OURS), not the whole catalog: the operator reading it is starting from zero.
def test_a_role_with_no_key_at_all_names_the_role_and_what_to_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for speaking in ("ELEVEN_API_KEY", "SONIOX_API_KEY", "DEEPGRAM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(speaking)
    down = doctor.first_failure(doctor.run_checks(load_settings(), probes_that_answer()))
    assert down is not None
    assert down.name == "provider keys"
    assert "tts" in down.detail
    assert "ELEVEN_API_KEY" in down.detail


def test_a_role_one_catalogued_vendor_can_answer_for_is_not_a_role_that_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forty-five vendors speak: a box with no ElevenLabs key and a Soniox one is not silent."""
    monkeypatch.delenv("ELEVEN_API_KEY")
    down = doctor.first_failure(doctor.run_checks(load_settings(), probes_that_answer()))
    assert down is None


def test_the_doctor_exits_zero_with_everything_up(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    a_box_that_posts_mail(monkeypatch)
    monkeypatch.setattr(doctor, "live_probes", probes_that_answer)
    assert main(["doctor"]) == 0
    printed = capsys.readouterr().out
    assert printed.count("✓") == len(doctor.CHECKS)
    assert "all up" in printed


def test_a_box_told_nothing_about_mail_reads_as_advice_and_never_as_the_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every door behaves as it did before mail existed, so a box without it is not a box down."""
    monkeypatch.setattr(doctor, "live_probes", probes_that_answer)
    assert main(["doctor"]) == 0
    printed = capsys.readouterr().out
    assert "! mail" in printed and "PINECALL_SMTP_URL" in printed and "all up" in printed


def test_the_mail_line_names_where_a_letter_goes_and_never_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report is read out loud and pasted into issues; the credential never travels with it."""
    monkeypatch.setenv("PINECALL_SMTP_URL", "smtp://AKIA:s3cret@relay.test:587")
    monkeypatch.setenv("PINECALL_MAIL_FROM", "Pinecall <no-reply@box.test>")
    line = named("mail", doctor.run_checks(load_settings(), probes_that_answer()))
    assert line.ok and "relay.test:587" in line.detail and "starttls" in line.detail
    assert "from the environment" in line.detail and "s3cret" not in line.detail


def test_the_mail_line_says_when_the_mailbox_is_the_one_the_operator_stored() -> None:
    """The same line, off the table: an operator who set one from /admin reads that it took."""
    stored = BoxMail(
        KeptMail(Mailbox("relay.acme.test", 465, "tls", "acme", "s3cret", "no-reply@acme.test")),
        "stored",
    )
    probes = probes_that_answer(the_boxs_mail=lambda _settings: stored)
    line = named("mail", doctor.run_checks(load_settings(), probes))
    assert line.ok and "relay.acme.test:465" in line.detail
    assert "stored by the operator" in line.detail and "s3cret" not in line.detail


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
    answer = named("provider keys answer", doctor.run_checks(load_settings(), probes_that_answer()))
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
        "api keys",
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
