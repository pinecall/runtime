"""What a quota answers: NULL is no limit, zero is a real one, and a cap is read two ways."""

import pytest

from pinecall.types import QUOTAS, Quotas
from pinecall.types.refused import DeclarationRefused

pytestmark = pytest.mark.unit


def test_an_org_nobody_limited_has_no_limit_on_anything() -> None:
    """A self-hosted box never writes a row, so every question it asks answers the same way."""
    quotas = Quotas()
    for name in QUOTAS:
        assert quotas.reached(name, 10_000) is None
        assert quotas.exceeded(name, 10_000) is None
        assert not quotas.switched_off(name)


def test_a_cap_is_reached_at_it_and_past_it_and_not_under_it() -> None:
    """The four flow quotas and the two stocks read the same: at the number, nothing more fits."""
    quotas = Quotas(memory_facts=100)
    assert quotas.reached("memory_facts", 99) is None
    assert quotas.reached("memory_facts", 100) == 100
    assert quotas.reached("memory_facts", 101) == 100


def test_a_push_fits_up_to_the_cap_and_not_one_chunk_past_it() -> None:
    """A push is judged whole, so filling a base exactly to the limit is allowed."""
    quotas = Quotas(knowledge_chunks=1000)
    assert quotas.exceeded("knowledge_chunks", 999) is None
    assert quotas.exceeded("knowledge_chunks", 1000) is None
    assert quotas.exceeded("knowledge_chunks", 1001) == 1000


def test_zero_refuses_everything_and_is_how_a_plan_says_it_has_no_such_feature() -> None:
    """Zero is not the absence of a limit: it is the limit that leaves room for nothing."""
    free = Quotas(memory_facts=0, knowledge_chunks=0)
    assert free.reached("memory_facts", 0) == 0
    assert free.exceeded("knowledge_chunks", 1) == 0
    assert free.switched_off("memory_facts") and free.switched_off("knowledge_chunks")


def test_a_cap_of_one_is_not_switched_off_and_no_cap_is_not_switched_off_either() -> None:
    """The fills read `switched_off` and nothing else: a full plan is not a plan without one."""
    assert not Quotas(memory_facts=1).switched_off("memory_facts")
    assert not Quotas().switched_off("knowledge_chunks")


def test_a_quota_is_a_count_and_a_negative_one_is_refused_by_name() -> None:
    with pytest.raises(DeclarationRefused, match="knowledge_chunks cannot be -1"):
        Quotas(knowledge_chunks=-1)


def test_the_names_are_spelled_once_and_the_dataclass_has_a_field_for_each() -> None:
    """QUOTAS is what the CLI builds its flags from and what the wire's enum is copied from."""
    assert QUOTAS == (
        "minutes",
        "messages",
        "agents",
        "concurrent_calls",
        "memory_facts",
        "knowledge_chunks",
        "numbers",
        "seats",
    )
    assert all(getattr(Quotas(), name) is None for name in QUOTAS)
