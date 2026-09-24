"""`pinecall-runtime gateway`: uvicorn over the app, and a stop that waits for what is in flight."""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from pinecall.cli import gateway

pytestmark = pytest.mark.unit


def test_the_gateway_is_run_with_a_graceful_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: dict[str, Any] = {}

    def run(app: str, **said: Any) -> None:
        ran.update(said, app=app)

    monkeypatch.setattr(gateway.uvicorn, "run", run)
    arguments = argparse.Namespace(host="127.0.0.1", port=8080, reload=False)
    assert gateway.run(arguments) == 0
    assert ran["app"] == gateway.APP
    assert ran["timeout_graceful_shutdown"] == gateway.GRACEFUL_S == 5
