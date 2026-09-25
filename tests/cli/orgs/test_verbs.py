"""`pinecall-runtime orgs`, verb by verb, against the real gateway over the ASGI transport."""

import io

import httpx
import pytest

from pinecall.cli.operator import Operator, OperatorRefused
from pinecall.cli.orgs.verbs import (
    a_key_from,
    a_lending_typed,
    add_org,
    list_orgs,
    list_provider_keys,
    remove_org,
    remove_provider_key,
    set_dialling,
    set_provider_key,
    set_quota,
)

pytestmark = pytest.mark.unit

# A tenant's own key, which no line this suite reads may ever contain.
A_TENANTS_KEY = "sk-the-clinic-brought-this-one"


@pytest.fixture
def operator(ops_http: httpx.AsyncClient) -> Operator:
    """The CLI's own client, on the gateway this test is running in the same loop."""
    return Operator(ops_http)


def printed() -> io.StringIO:
    """Where a verb writes, so a test reads the terminal instead of capturing a stream."""
    return io.StringIO()


async def test_add_prints_the_minted_id_and_list_shows_the_default_org_first(
    operator: Operator,
) -> None:
    out = printed()
    assert await add_org("tienda-sur", "Tienda Sur", operator, out) == 0
    id, slug, *name = out.getvalue().split()
    assert id.startswith("org_") and slug == "tienda-sur" and " ".join(name) == "Tienda Sur"
    listing = printed()
    assert await list_orgs(operator, listing) == 0
    lines = listing.getvalue().splitlines()
    assert lines[0].startswith("default")
    assert any(id in line for line in lines)


async def test_quota_prints_every_limit_and_a_dash_for_the_ones_left_open(
    operator: Operator,
) -> None:
    out = printed()
    limits: dict[str, int | None] = {
        "minutes": 120,
        "messages": None,
        "agents": 2,
        "concurrent_calls": None,
        "memory_facts": 0,
        "knowledge_chunks": 5000,
    }
    assert await set_quota("clinica", limits, operator, out) == 0
    said = out.getvalue()
    assert "minutes" in said and "120" in said
    assert "messages" in said and "—" in said
    assert "knowledge_chunks" in said and "5000" in said
    assert "memory_facts      0" in said, "zero is a limit and prints as one, never as a dash"
    assert "lends             every key of the box" in said, "left out lends every key"


def test_the_lending_typed_is_entries_none_is_the_empty_set_and_nothing_is_every_key() -> None:
    assert a_lending_typed("deepgram, anthropic/claude-haiku-4-5") == [
        "deepgram",
        "anthropic/claude-haiku-4-5",
    ]
    assert a_lending_typed("none") == []
    assert a_lending_typed(None) is None


async def test_quota_prints_what_the_box_lends_as_the_door_kept_it(operator: Operator) -> None:
    out = printed()
    lending = {"lends": ["deepgram", "claude/claude-haiku-4-5"]}
    assert await set_quota("clinica", lending, operator, out) == 0
    assert "lends             anthropic/claude-haiku-4-5, deepgram" in out.getvalue()
    nothing = printed()
    assert await set_quota("clinica", {"lends": []}, operator, nothing) == 0
    assert "lends             none" in nothing.getvalue()


async def test_dialling_prints_the_four_guards_the_door_kept_and_no_country(
    operator: Operator,
) -> None:
    out = printed()
    said = {"dial_anywhere": False, "per_minute": 3, "per_day": None, "max_duration_s": None}
    assert await set_dialling("clinica", said, operator, out) == 0
    lines = out.getvalue().splitlines()
    assert [line.split()[0] for line in lines] == [
        "dial_anywhere",
        "per_minute",
        "per_day",
        "max_duration_s",
    ]
    assert "  per_minute        3" in lines
    assert "countr" not in out.getvalue()


async def test_rm_on_an_org_nobody_typed_is_a_refusal_a_person_can_read(
    operator: Operator,
) -> None:
    with pytest.raises(OperatorRefused, match="404: no org named nobody"):
        await remove_org("nobody", operator, printed())


async def test_rm_takes_an_unused_org_away(operator: Operator) -> None:
    await add_org("tienda-sur", None, operator, printed())
    out = printed()
    assert await remove_org("tienda-sur", operator, out) == 0
    assert "removed" in out.getvalue()
    with pytest.raises(OperatorRefused, match="404"):
        await remove_org("tienda-sur", operator, printed())


async def test_provider_key_set_says_which_vendor_and_never_the_key(operator: Operator) -> None:
    """The one CLI verb that handles a secret: nothing it prints carries a character of it."""
    out = printed()
    assert await set_provider_key("clinica", "elevenlabs", A_TENANTS_KEY, operator, out) == 0
    said = out.getvalue()
    assert "elevenlabs" in said and "clinica" in said
    assert A_TENANTS_KEY not in said


async def test_provider_key_list_prints_the_vendors_and_says_so_when_there_are_none(
    operator: Operator,
) -> None:
    empty = printed()
    assert await list_provider_keys("clinica", operator, empty) == 0
    assert "keys of this box" in empty.getvalue()
    await set_provider_key("clinica", "soniox", A_TENANTS_KEY, operator, printed())
    listed = printed()
    assert await list_provider_keys("clinica", operator, listed) == 0
    assert listed.getvalue().split() == ["soniox"]
    assert A_TENANTS_KEY not in listed.getvalue()


async def test_provider_key_rm_puts_the_org_back_on_the_box_and_a_typo_is_a_refusal(
    operator: Operator,
) -> None:
    await set_provider_key("clinica", "openai", A_TENANTS_KEY, operator, printed())
    out = printed()
    assert await remove_provider_key("clinica", "openai", operator, out) == 0
    assert "back on this box's openai key" in out.getvalue()
    with pytest.raises(OperatorRefused, match="404: org clinica has no openai key"):
        await remove_provider_key("clinica", "openai", operator, printed())


def test_the_key_is_read_from_stdin_and_a_blank_line_is_nothing() -> None:
    """argv is in `ps`; a key that arrived as a flag would be a key in somebody's history."""
    assert a_key_from(io.StringIO(f"{A_TENANTS_KEY}\n"), "elevenlabs") == A_TENANTS_KEY
    assert a_key_from(io.StringIO("\n"), "elevenlabs") is None
    assert a_key_from(io.StringIO(""), "elevenlabs") is None
