"""How a doctor test asks: a stack where everything answers, and one line of the report by name."""

from collections.abc import Callable, Mapping

import pytest

from pinecall.cli.doctor import verbs as doctor
from pinecall.cli.doctor.probes import Probes


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


# The mail line is the one check a box passes only by having been TOLD something, and ring 0 is
# told nothing: a report where everything answers has to say where a letter would go. The relay
# is a port nothing listens on, like every other sentinel in this suite.
def a_box_that_posts_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    """A box with a mail server declared. Nothing is sent: the doctor only reads the setting."""
    monkeypatch.setenv("PINECALL_SMTP_URL", "smtp://127.0.0.1:1")
    monkeypatch.setenv("PINECALL_MAIL_FROM", "Pinecall <no-reply@dead.sentinel>")


def named(name: str, results: list[doctor.Result]) -> doctor.Result:
    """One line of the report, by what it is called: a check's place in the table is not a name."""
    found = next((result for result in results if result.name == name), None)
    assert found is not None, f"no check called {name}: {[result.name for result in results]}"
    return found
