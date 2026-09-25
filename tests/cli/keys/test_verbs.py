"""`pinecall-runtime keys`, verb by verb, against the real gateway over the ASGI transport."""

import io

import httpx
import pytest

from pinecall.auth.keys import PRODUCTION_PREFIX, fingerprint
from pinecall.cli.keys.verbs import issue_key, list_keys, revoke_key
from pinecall.cli.operator import Operator, OperatorRefused

pytestmark = pytest.mark.unit

ORG = "clinica"


@pytest.fixture
def operator(ops_http: httpx.AsyncClient) -> Operator:
    """The CLI's own client, on the gateway this test is running in the same loop."""
    return Operator(ops_http)


def printed() -> io.StringIO:
    """Where a verb writes, so a test reads the terminal instead of capturing a stream."""
    return io.StringIO()


async def issued(operator: Operator, label: str | None = None) -> str:
    """One key through the verb a person types, read back off the terminal's first line."""
    out = printed()
    assert await issue_key(ORG, label, operator, out) == 0
    return out.getvalue().splitlines()[0]


async def test_issue_prints_the_key_alone_on_stdout_and_the_words_about_it_on_stderr(
    operator: Operator, capsys: pytest.CaptureFixture[str]
) -> None:
    """stdout IS the key — a unit pipes it straight into systemd-creds — and a person reads both."""
    out = printed()
    assert await issue_key(ORG, "the worker on this box", operator, out) == 0
    assert out.getvalue().splitlines() == [out.getvalue().strip()]
    key = out.getvalue().strip()
    assert key.startswith(PRODUCTION_PREFIX) and " " not in key
    said = capsys.readouterr().err.splitlines()
    assert f"org {ORG}" in said[0]
    assert "production" in said[0]
    assert "the worker on this box" in said[0]
    assert said[1].strip() == "every scope"
    assert "never shown again" in said[2]


async def test_issue_takes_the_scopes_and_the_person_and_the_listing_shows_them(
    operator: Operator, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--scope calls --scope talk --subject m_1 --name Berna`, as typed."""
    out = printed()
    assert (
        await issue_key(
            ORG,
            "berna's laptop",
            operator,
            out,
            scopes=["talk", "calls"],
            subject="m_1",
            name="Berna",
        )
        == 0
    )
    said = capsys.readouterr().err.splitlines()
    assert "production" in said[0]
    assert said[1].strip() == "scopes calls · talk"
    listing = printed()
    await list_keys(ORG, operator, listing)
    assert "Berna" in listing.getvalue()


async def test_issue_refuses_the_other_world_in_the_gateways_words(operator: Operator) -> None:
    """An instance mints its own world's keys: `--env sandbox` at production is a refusal."""
    with pytest.raises(OperatorRefused, match="400.*sandbox"):
        await issue_key(ORG, None, operator, printed(), env="sandbox")


async def test_issue_refuses_a_world_that_is_not_one_in_the_gateways_words(
    operator: Operator,
) -> None:
    with pytest.raises(OperatorRefused, match="400.*staging"):
        await issue_key(ORG, None, operator, printed(), env="staging")


async def test_list_says_so_when_the_org_has_no_key_at_all(operator: Operator) -> None:
    """An empty screen is not an answer: a fresh box is told which verb makes one."""
    out = printed()
    assert await list_keys("default", operator, out) == 0
    assert "keys issue --org default" in out.getvalue()


async def test_list_prints_the_fingerprint_and_never_the_key(operator: Operator) -> None:
    """The whole promise of the group: the key was shown once, at issue, and nowhere else."""
    key = await issued(operator, "the worker on this box")
    out = printed()
    assert await list_keys(ORG, operator, out) == 0
    said = out.getvalue()
    assert fingerprint(key) in said
    assert key not in said
    assert "the worker on this box" in said
    assert "live" in said


async def test_revoke_keeps_the_row_and_the_listing_says_revoked(operator: Operator) -> None:
    """A row that vanished would make the log entries that name the key unreadable."""
    key = await issued(operator)
    out = printed()
    assert await revoke_key(fingerprint(key), operator, out) == 0
    assert "the row stays" in out.getvalue()
    listing = printed()
    await list_keys(ORG, operator, listing)
    assert fingerprint(key) in listing.getvalue()
    assert "revoked" in listing.getvalue()


async def test_revoking_a_fingerprint_nobody_answers_to_is_a_refusal(operator: Operator) -> None:
    """`keys revoke` on a typo must not read as done, so the refusal reaches the exit code."""
    with pytest.raises(OperatorRefused, match="404"):
        await revoke_key("a" * 64, operator, printed())
