"""Tests for an org and its quotas."""

from dataclasses import fields
from typing import get_args

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.org import QUOTAS, QuotaName, Quotas
from pinecall.wire import events as wire_events


def test_an_org_nobody_limited_has_no_limit_on_anything() -> None:
    quotas = Quotas()
    for name in QUOTAS:
        assert quotas.reached(name, 10_000) is None
        assert quotas.exceeded(name, 10_000) is None
        assert not quotas.switched_off(name)


def test_a_cap_is_reached_at_it_and_past_it_and_not_under_it() -> None:
    quotas = Quotas(memory_facts=100)
    assert quotas.reached("memory_facts", 99) is None
    assert quotas.reached("memory_facts", 100) == 100
    assert quotas.reached("memory_facts", 101) == 100


def test_a_push_fits_up_to_the_cap_and_not_one_chunk_past_it() -> None:
    quotas = Quotas(knowledge_chunks=1000)
    assert quotas.exceeded("knowledge_chunks", 999) is None
    assert quotas.exceeded("knowledge_chunks", 1000) is None
    assert quotas.exceeded("knowledge_chunks", 1001) == 1000


def test_zero_refuses_everything_and_is_how_a_plan_says_it_has_no_such_feature() -> None:
    free = Quotas(memory_facts=0, knowledge_chunks=0)
    assert free.reached("memory_facts", 0) == 0
    assert free.exceeded("knowledge_chunks", 1) == 0
    assert free.switched_off("memory_facts")
    assert free.switched_off("knowledge_chunks")


def test_a_cap_of_one_is_not_switched_off_and_no_cap_is_not_switched_off_either() -> None:
    assert not Quotas(memory_facts=1).switched_off("memory_facts")
    assert not Quotas().switched_off("knowledge_chunks")


def test_a_quota_is_a_count_and_a_negative_one_is_refused_by_name() -> None:
    with pytest.raises(DeclarationRefused, match="knowledge_chunks cannot be -1"):
        Quotas(knowledge_chunks=-1)
    with pytest.raises(DeclarationRefused, match="budget is euros"):
        Quotas(budget_eur=-1)


def test_the_quota_names_are_spelled_once_and_the_dataclass_has_a_field_for_each() -> None:
    assert set(QUOTAS) == set(Quotas().limits)
    assert set(QUOTAS) <= {declared.name for declared in fields(Quotas)}
    assert all(limit is None for limit in Quotas().limits.values())


def test_the_quota_names_here_are_the_ones_a_refusal_may_say() -> None:
    assert wire_events.CreditsExhausted.model_fields["quota"].annotation is QuotaName
    assert set(QUOTAS) == set(get_args(QuotaName.__value__))
