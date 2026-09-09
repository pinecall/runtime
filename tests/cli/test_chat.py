"""`pinecall-runtime chat`: the address it dials and the key it carries, with no gateway at all."""

import pytest

from pinecall.cli import build_parser
from pinecall.cli.chat import socket_url

pytestmark = pytest.mark.unit


def test_the_socket_address_flips_the_scheme_and_names_the_agent() -> None:
    assert socket_url("http://localhost:8080/", "clinica-norte", None) == (
        "ws://localhost:8080/v1/chat?agent=clinica-norte"
    )
    assert socket_url("https://box.example", "clinica-norte", "web_1").startswith("wss://")
    assert socket_url("https://box.example", "clinica-norte", "web_1").endswith("&caller=web_1")


def test_chat_requires_an_agent() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["chat"])
    assert build_parser().parse_args(["chat", "--agent", "x"]).agent == "x"
