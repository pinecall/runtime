"""Tests for the traceback verb: the lines a carrier is answered with, and a day that is none."""

import argparse

import pytest

from pinecall.cli._traceback import lines_of, traced
from pinecall.domain.errors import DeclarationRefused
from pinecall.process.settings import Settings
from pinecall.tenancy.traceback import CallRecord, DialRecord, Traceback
from tests.conftest import DSN

DANA = "+14155550142"

A_DAY = 1790208000.0  # 2026-09-24 00:00 UTC

ERASED = CallRecord(
    call="CA_1",
    org="org_a",
    env="production",
    direction="outbound",
    from_number="+14155550100",
    to_number=DANA,
    started_at=A_DAY + 3600,
    ended_at=A_DAY + 3672,
    end_reason="agent_hung_up",
    erased=True,
)

REFUSED = DialRecord(
    org="org_a",
    env="production",
    agent="agenda",
    call=None,
    shown="+14155550100",
    asked_by="m_ana",
    refused="do_not_call",
    at=A_DAY + 7200,
)


def test_a_traceback_prints_each_call_and_each_dial_with_who_and_how_it_ended() -> None:
    lines = lines_of(Traceback(DANA, [ERASED], [REFUSED]), A_DAY)
    assert lines[0] == f"{DANA} since 2026-09-24"
    assert "+14155550100 -> +14155550142" in lines[2]
    assert "72s" in lines[2]
    assert lines[2].endswith("CA_1  erased, record kept")
    assert lines[4].endswith("asked by m_ana  refused: do_not_call")


def test_a_number_with_nothing_says_so() -> None:
    assert lines_of(Traceback(DANA, [], []), A_DAY) == [
        f"no call with {DANA} and no dial to it since 2026-09-24"
    ]


def test_a_since_that_is_no_day_is_refused_before_the_database_is_asked() -> None:
    args = argparse.Namespace(number=DANA, since="last week")
    with pytest.raises(DeclarationRefused, match="a day, like"):
        traced(Settings.model_validate({"DATABASE_URL": DSN}), args)
