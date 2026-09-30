"""Tests for the bodies of the hosting doors."""

import pytest
from pydantic import ValidationError

from pinecall.wire.rest.hosting import HostedAppRow, PutSecretRequest, RunnerReport


def test_an_app_nobody_released_yet_says_so_with_a_null() -> None:
    row = HostedAppRow(
        name="support",
        release=None,
        live_release=None,
        failed_why=None,
        created_by="m_ana",
        created_at=1.0,
    )
    assert row.written()["release"] is None


def test_a_secret_is_written_with_its_value_and_nothing_else() -> None:
    assert PutSecretRequest.model_validate({"value": "x"}).value == "x"
    with pytest.raises(ValidationError):
        PutSecretRequest.model_validate({"value": "x", "name": "CRM_TOKEN"})


def test_a_report_says_live_or_failed_and_nothing_else() -> None:
    report = {"org": "org_1", "name": "support", "host": "support-r1-abcdef12"}
    assert RunnerReport.model_validate({**report, "state": "live"}).why == ""
    with pytest.raises(ValidationError):
        RunnerReport.model_validate({**report, "state": "building"})
