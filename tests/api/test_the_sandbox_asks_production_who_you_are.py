"""A sandbox signs a person in: production redeems the code, the rows mirrored, a day's key."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.accounts.identity import NOT_ACTIVE, SLUG_HELD_HERE, the_identity
from pinecall.api.accounts.login import NO_CODE, NOT_A_MEMBER
from pinecall.api.app import app
from pinecall.auth.identity import REDEEM, UNREACHABLE, Identity
from pinecall.auth.keys import MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.auth.person_keys import SANDBOX_PERSONS_KEY_LIFE
from pinecall.extensions import Extensions
from pinecall.orgs.records import MemoryOrgs
from pinecall.types import ROLE_SCOPES, SANDBOX, Member, Org, Quotas
from tests.api.talking import answering_in, at_the_console
from tests.conftest import THE_IDENTITY

pytestmark = pytest.mark.unit

LOGIN = "/v1/login"
A_CODE = "lc_minted_at_production"

# Production's rows, by production's ids: what a redemption answers about Berna.
TIENDA = Org(id="org_4ad9", slug="tienda", name="Tienda Sur")
BERNA = Member(
    id="m_berna",
    org=TIENDA.id,
    email="berna@tienda.uy",
    name="Berna",
    role="developer",
    agents=frozenset({"tienda-sur"}),
    status="active",
)


def redeemed(org: Org = TIENDA, member: Member = BERNA) -> dict[str, Any]:
    """Production's answer, in the shape its door says it."""
    return {
        "org": {"id": org.id, "slug": org.slug, "name": org.name},
        "member": {
            "id": member.id,
            "email": member.email,
            "name": member.name,
            "role": member.role,
            "agents": sorted(member.agents),
            "status": member.status,
        },
    }


class Production:
    """Production's redemption door, scripted: one answer, and every code it was asked for."""

    def __init__(self) -> None:
        self.answer: httpx.Response | Exception = httpx.Response(200, json=redeemed())
        self.asked: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{THE_IDENTITY}{REDEEM}"
        self.asked.append(json.loads(request.content)["code"])
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def production() -> Iterator[Production]:
    """The gateway the sandbox asks, at the URL the sandbox was told, over a scripted wire."""
    scripted = Production()
    client = httpx.AsyncClient(transport=httpx.MockTransport(scripted.handle))
    app.dependency_overrides[the_identity] = lambda: Identity(client, THE_IDENTITY)
    yield scripted
    app.dependency_overrides.pop(the_identity, None)


@pytest.fixture
def sandbox(settings: Settings, wired: None) -> Settings:  # noqa: ARG001
    """The test's gateway is a sandbox instance, asking THE_IDENTITY who a person is."""
    return answering_in(SANDBOX, settings)


async def signed_in(stranger: httpx.AsyncClient) -> dict[str, Any]:
    answer = await stranger.post(LOGIN, json={"code": A_CODE, "device": "console"})
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_a_first_sign_in_mirrors_the_org_and_the_member_and_mints_a_days_key(
    sandbox: Settings,  # noqa: ARG001
    production: Production,
    stranger: httpx.AsyncClient,
    orgs: MemoryOrgs,
    members: MemoryMembers,
    keys: MemoryKeys,
) -> None:
    before = datetime.now(UTC)
    signed = await signed_in(stranger)
    assert production.asked == [A_CODE]
    assert await orgs.find("tienda") == TIENDA, "production's id and slug, so every word holds"
    mirrored = await members.find(TIENDA.id, BERNA.id)
    assert mirrored is not None
    assert (mirrored.role, mirrored.status, mirrored.verified) == ("developer", "active", True)
    assert (await members.by_email(TIENDA.id, BERNA.email)) is not None
    assert (await members.a_persons_password(BERNA.email)) is None, "a sandbox keeps none"
    assert (signed["org"], signed["subject"], signed["label"]) == (TIENDA.id, BERNA.id, "console")
    assert signed["scopes"] == sorted(ROLE_SCOPES["developer"])
    record = await keys.verify(signed["key"])
    assert record is not None and record.expires_at is not None
    assert before + SANDBOX_PERSONS_KEY_LIFE <= record.expires_at


# The trial lives in the sandbox, and a sandbox org is born here by mirroring: the extension is
# asked in the sandbox's world when the org is new to this instance, and never again after.
async def test_an_org_new_to_the_sandbox_is_admitted_once_in_the_sandboxs_world(
    sandbox: Settings,  # noqa: ARG001
    production: Production,  # noqa: ARG001
    stranger: httpx.AsyncClient,
    orgs: MemoryOrgs,
    extensions: Extensions,
) -> None:
    a_trial = Quotas(minutes=30)
    asked: list[tuple[str, str, str, int]] = []

    def admitted(org: Org, email: str, world: str, already: int) -> Quotas:
        asked.append((org.slug, email, world, already))
        return a_trial

    extensions.admitted = admitted
    await signed_in(stranger)
    await signed_in(stranger)
    assert asked == [(TIENDA.slug, BERNA.email, SANDBOX, 0)], (
        "once, and never on the second sign-in"
    )
    assert await orgs.quotas_of(TIENDA.id) == a_trial


async def test_an_org_the_sandbox_already_held_is_never_admitted(
    sandbox: Settings,  # noqa: ARG001
    production: Production,  # noqa: ARG001
    stranger: httpx.AsyncClient,
    orgs: MemoryOrgs,
    extensions: Extensions,
) -> None:
    """What the seed copied, or any org here before the trial existed, keeps what it had."""
    await orgs.mirrored(TIENDA)
    asked: list[str] = []

    def admitted(org: Org, email: str, world: str, already: int) -> Quotas:  # noqa: ARG001
        asked.append(org.slug)
        return Quotas(minutes=30)

    extensions.admitted = admitted
    await signed_in(stranger)
    assert asked == []
    assert await orgs.quotas_of(TIENDA.id) == Quotas()


async def test_a_second_sign_in_takes_the_role_production_says_now(
    sandbox: Settings,  # noqa: ARG001
    production: Production,
    stranger: httpx.AsyncClient,
    members: MemoryMembers,
) -> None:
    await signed_in(stranger)
    production.answer = httpx.Response(200, json=redeemed(member=replace(BERNA, role="qa")))
    signed = await signed_in(stranger)
    assert signed["scopes"] == sorted(ROLE_SCOPES["qa"])
    mirrored = await members.find(TIENDA.id, BERNA.id)
    assert mirrored is not None and mirrored.role == "qa"


@pytest.mark.parametrize(
    ("status", "said"),
    [(403, NOT_A_MEMBER), (404, NO_CODE), (401, "this door takes an API key")],
)
async def test_what_production_refuses_is_refused_here_in_its_own_words(
    sandbox: Settings,  # noqa: ARG001
    production: Production,
    stranger: httpx.AsyncClient,
    orgs: MemoryOrgs,
    status: int,
    said: str,
) -> None:
    """A member production disabled, a code spent there: production knows why, and says it."""
    production.answer = httpx.Response(status, json={"detail": said})
    refused = await stranger.post(LOGIN, json={"code": A_CODE})
    assert (refused.status_code, refused.json()["detail"]) == (status, said)
    assert await orgs.find("tienda") is None, "nothing is mirrored for a refusal"


async def test_production_not_answering_is_502_in_one_sentence(
    sandbox: Settings,  # noqa: ARG001
    production: Production,
    stranger: httpx.AsyncClient,
) -> None:
    production.answer = httpx.ConnectError("connection refused")
    refused = await stranger.post(LOGIN, json={"code": A_CODE})
    assert (refused.status_code, refused.json()["detail"]) == (
        502,
        UNREACHABLE.format(url=THE_IDENTITY),
    )


async def test_a_slug_an_org_of_the_sandboxs_own_holds_is_not_taken_from_it(
    sandbox: Settings,  # noqa: ARG001
    production: Production,  # noqa: ARG001
    stranger: httpx.AsyncClient,
    orgs: MemoryOrgs,
) -> None:
    ours = await orgs.create("tienda", "a tienda of this sandbox's own")
    refused = await stranger.post(LOGIN, json={"code": A_CODE})
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        SLUG_HELD_HERE.format(slug="tienda"),
    )
    assert await orgs.find("tienda") == ours


async def test_a_stale_mirror_of_the_address_goes_and_its_keys_stop(
    sandbox: Settings,  # noqa: ARG001
    production: Production,
    stranger: httpx.AsyncClient,
    members: MemoryMembers,
    keys: MemoryKeys,
) -> None:
    """Production removed Berna and invited her again: a new id, and the old row still here."""
    before = replace(BERNA, id="m_berna_before")
    production.answer = httpx.Response(200, json=redeemed(member=before))
    old_key = (await signed_in(stranger))["key"]
    production.answer = httpx.Response(200, json=redeemed())
    signed = await signed_in(stranger)
    assert signed["subject"] == BERNA.id
    assert await members.find(TIENDA.id, before.id) is None
    assert await keys.verify(old_key) is None, "the removed person's key opens nothing"


async def test_a_member_production_disabled_is_mirrored_disabled_and_loses_every_key_here(
    sandbox: Settings,  # noqa: ARG001
    production: Production,
    stranger: httpx.AsyncClient,
    members: MemoryMembers,
) -> None:
    """At the next sign-in, not in a day: the key she already holds is 401 from then on."""
    earlier = (await signed_in(stranger))["key"]
    production.answer = httpx.Response(200, json=redeemed(member=replace(BERNA, status="disabled")))
    refused = await stranger.post(LOGIN, json={"code": A_CODE})
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        NOT_ACTIVE.format(email=BERNA.email, slug="tienda"),
    )
    mirrored = await members.find(TIENDA.id, BERNA.id)
    assert mirrored is not None and mirrored.status == "disabled"
    async with at_the_console(earlier, SANDBOX) as berna:
        assert (await berna.get("/v1/whoami")).status_code == 401


async def test_the_org_switch_lists_only_the_orgs_signed_into_here(
    sandbox: Settings,  # noqa: ARG001
    production: Production,  # noqa: ARG001
    stranger: httpx.AsyncClient,
) -> None:
    """The mirror is what the sandbox knows: an org of hers at production she never signed into
    here is not listed, and production's own switcher lists that one."""
    signed = await signed_in(stranger)
    async with at_the_console(signed["key"], SANDBOX) as berna:
        listed = (await berna.get("/v1/login/orgs")).json()["orgs"]
    assert [(row["org"], row["slug"], row["here"]) for row in listed] == [
        (TIENDA.id, "tienda", True)
    ]
