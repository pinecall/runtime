"""Tests for the TXT record that proves a domain an org's SSO admits is the org's."""

import pytest

from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.postgres.pool import Pool
from pinecall.tenancy import sso_domains
from pinecall.tenancy.sso_domains import TXT_PREFIX
from tests.conftest import TXT_RECORDS, postgres
from tests.tenancy.test_signin import org_of


@postgres
async def test_each_domain_gets_a_token_once_and_a_domain_dropped_is_forgotten(pool: Pool) -> None:
    org = await org_of(pool)
    first = await sso_domains.proofs_for(pool, org.id, ("clinica.test", "clinica.uy"))
    again = await sso_domains.proofs_for(pool, org.id, ("clinica.uy",))
    assert [(proof.domain, proof.verified) for proof in first] == [
        ("clinica.test", False),
        ("clinica.uy", False),
    ]
    assert [proof.domain for proof in again] == ["clinica.uy"]
    assert again[0].token == first[1].token, "a token stands however often the domains are said"
    assert again[0].txt == f"{TXT_PREFIX}{again[0].token}"
    assert await sso_domains.verified_domains(pool, org.id) == frozenset()


@postgres
async def test_a_record_published_at_the_domain_verifies_it_and_a_missing_one_says_what_is_there(
    pool: Pool,
) -> None:
    org = await org_of(pool)
    (proof,) = await sso_domains.proofs_for(pool, org.id, ("clinica.test",))
    with pytest.raises(DeclarationRefused, match="no pinecall-verify record"):
        await sso_domains.verify(pool, org.id, "clinica.test")
    TXT_RECORDS["clinica.test"] = ["v=spf1 -all", f"{TXT_PREFIX}somebody-elses"]
    with pytest.raises(DeclarationRefused, match="pinecall-verify=somebody-elses"):
        await sso_domains.verify(pool, org.id, "clinica.test")
    TXT_RECORDS["clinica.test"].append(proof.txt)
    seen = await sso_domains.verify(pool, org.id, "clinica.test")
    assert seen.verified
    assert await sso_domains.verified_domains(pool, org.id) == frozenset({"clinica.test"})
    # Seen once, it stands: the record may go, the proof does not.
    TXT_RECORDS.clear()
    assert (await sso_domains.verify(pool, org.id, "clinica.test")).verified


@postgres
async def test_a_domain_the_sso_does_not_name_is_nobodys_to_verify(pool: Pool) -> None:
    org = await org_of(pool)
    with pytest.raises(NotFound, match="not a domain of this org's SSO"):
        await sso_domains.verify(pool, org.id, "other.test")
