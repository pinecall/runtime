"""Who a written caller is: the id the chat socket may name, and what memory files under it."""

from typing import cast

import pytest
from starlette.websockets import WebSocket

from pinecall.api.calls.chat import a_call_from
from pinecall.types import PRODUCTION
from tests.api.conftest import AGENT

pytestmark = pytest.mark.unit


# Memory needs an identity, and a web visitor has none until somebody says who they are. In
# production the token door seals a contact id the browser cannot forge; on this socket the org's
# own key says it, which is how a developer exercises memory before there is a token at all.
def test_a_chat_that_names_a_contact_is_a_call_memory_can_file() -> None:
    said = a_call_from(
        _asked({"agent": AGENT, "contact": "+34600123456"}), "clinica", PRODUCTION, AGENT
    )
    assert said.remembered_as == "+34600123456"


def test_a_chat_that_names_nobody_is_a_call_memory_files_under_nothing() -> None:
    said = a_call_from(_asked({"agent": AGENT}), "clinica", PRODUCTION, AGENT)
    assert said.contact is None
    assert said.remembered_as is None


def test_the_visitor_id_is_still_the_calling_side_when_a_contact_is_named() -> None:
    said = a_call_from(_asked({"agent": AGENT, "contact": "c_9"}), "clinica", PRODUCTION, AGENT)
    assert said.caller.startswith("web_")


class _QueryOnly:
    """A socket with nothing but its query string, which is all the context minting reads."""

    def __init__(self, params: dict[str, str]) -> None:
        self.query_params = params


def _asked(params: dict[str, str]) -> WebSocket:
    """That socket, as the minting's signature asks for it: nothing else of a WebSocket is read."""
    return cast(WebSocket, _QueryOnly(params))
