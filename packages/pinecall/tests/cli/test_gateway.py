"""`pinecall-runtime gateway`: uvicorn over the app, bound where its URL says, stopped gently."""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.config
import signal
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from livekit.agents.cli.log import JsonFormatter

from pinecall.api.app import app
from pinecall.api.sse import new_closing
from pinecall.cli import gateway
from pinecall.settings import load_settings

pytestmark = pytest.mark.unit


def ran_with(
    monkeypatch: pytest.MonkeyPatch, url: str, *, host: str | None = None, port: int | None = None
) -> dict[str, Any]:
    """What uvicorn's server was configured with, for a gateway whose URL and flags say these."""
    ran: dict[str, Any] = {}

    def run(server: gateway.GatewayServer) -> None:
        config = server.config
        ran.update(
            app=config.app,
            host=config.host,
            port=config.port,
            log_config=config.log_config,
            timeout_graceful_shutdown=config.timeout_graceful_shutdown,
        )

    monkeypatch.setattr(gateway.GatewayServer, "run", run)
    monkeypatch.setenv("PINECALL_GATEWAY_URL", url)
    arguments = argparse.Namespace(host=host, port=port, reload=False)
    assert gateway.run(arguments) == 0
    return ran


def test_the_gateway_is_run_with_a_graceful_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    ran = ran_with(monkeypatch, "http://127.0.0.1:8080")
    assert ran["app"] is app
    assert ran["timeout_graceful_shutdown"] == gateway.GRACEFUL_S == 5


def test_uvicorn_is_handed_the_log_config_so_a_reloaded_child_writes_the_same_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_LOG_LEVEL", "debug")
    ran = ran_with(monkeypatch, "http://127.0.0.1:8080")
    assert ran["log_config"]["root"] == {"level": "DEBUG", "handlers": ["stdout"]}
    assert ran["log_config"]["loggers"]["httpx"] == {"level": "WARNING"}


def test_a_journal_reads_the_workers_own_json_line_and_a_terminal_reads_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_LOG_FORMAT", "json")
    line = _one_line_under(gateway.build_log_config(load_settings()))
    assert line.startswith('{"message": "hello 2", "level": "INFO", "name": "pinecall.a"')
    monkeypatch.setenv("PINECALL_LOG_FORMAT", "text")
    line = _one_line_under(gateway.build_log_config(load_settings()))
    assert line.endswith("INFO    pinecall.a hello 2")
    assert isinstance(gateway.build_log_config(load_settings())["formatters"]["line"], dict)


def _one_line_under(config: dict[str, Any]) -> str:
    """What logging writes for one record once the config is applied — and applied, it is valid.
    The root logger's handlers are put back so the suite's own capture is untouched."""
    root = logging.getLogger()
    kept, level = list(root.handlers), root.level
    try:
        logging.config.dictConfig(config)
        formatter = root.handlers[0].formatter
        assert formatter is not None
        if config["formatters"]["line"].get("()") is JsonFormatter:
            assert isinstance(formatter, JsonFormatter)
        record = logging.LogRecord("pinecall.a", logging.INFO, "a.py", 1, "hello %d", (2,), None)
        return formatter.format(record)
    finally:
        root.handlers[:] = kept
        root.setLevel(level)


def test_a_gateway_told_nothing_binds_the_host_and_port_of_its_own_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran = ran_with(monkeypatch, "http://127.0.0.1:8180")
    assert (ran["host"], ran["port"]) == ("127.0.0.1", 8180)


def test_a_flag_wins_over_its_half_of_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    ran = ran_with(monkeypatch, "http://127.0.0.1:8180", host="0.0.0.0")  # noqa: S104 — said on purpose
    assert (ran["host"], ran["port"]) == ("0.0.0.0", 8180)  # noqa: S104


def test_both_flags_need_no_url_a_gateway_could_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    ran = ran_with(monkeypatch, "https://box.example.com", host="127.0.0.1", port=9000)
    assert (ran["host"], ran["port"]) == ("127.0.0.1", 9000)


@pytest.mark.parametrize(
    "url", ["http://[::1]:8280", "http://localhost:8080", "http://127.0.0.2:8380"]
)
def test_every_spelling_of_loopback_is_bound(url: str) -> None:
    assert gateway.bind_address(url)[1] in {8080, 8280, 8380}


@pytest.mark.parametrize(
    "url", ["https://box.example.com", "http://10.0.0.4:8080", "http://127.0.0.1", "http://:x"]
)
def test_a_url_with_no_loopback_port_is_refused_in_one_sentence(url: str) -> None:
    with pytest.raises(gateway.NotBindable) as refused:
        gateway.bind_address(url)
    assert str(refused.value) == gateway.NOT_HERE.format(variable="PINECALL_GATEWAY_URL", url=url)


# A stream is a request that never finishes by itself, and uvicorn waits for every request before
# it stops: the server's first act on a stop is to tell the streams, which then end on their own.
async def test_a_stop_tells_the_open_streams_before_uvicorn_stops() -> None:
    stopping = FastAPI()
    closing = new_closing(stopping)
    server = gateway.GatewayServer(uvicorn.Config(stopping), stopping)
    server.handle_exit(signal.SIGTERM, None)
    await asyncio.wait_for(closing.wait(), timeout=1)
    assert server.should_exit
