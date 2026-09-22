"""Who a written caller is: the id the chat socket may name, and who is being played on it."""

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


# The written half of the seam that names a synthetic caller. `pinecall simulate` drives the turns
# from the terminal, so without this nothing on the call said who the model was playing, and the
# Personas screen could show what a caller is and nothing about what it has done. The spoken half
# is the dispatch's `persona` (types/dispatch.py), because there the worker writes call.started.
def test_a_chat_that_names_a_persona_is_a_call_that_says_who_is_being_played() -> None:
    said = a_call_from(
        _asked({"agent": AGENT, "persona": "homeowner"}), "clinica", PRODUCTION, AGENT
    )
    assert said.persona == "homeowner"


def test_a_persons_chat_names_no_persona_because_nobody_is_playing_anybody() -> None:
    said = a_call_from(_asked({"agent": AGENT}), "clinica", PRODUCTION, AGENT)
    assert said.persona is None


# And the rule the persona wrote for the call rides the same context to call.started, where the
# `persona` judge reads it at hang-up — read off the org's list by the door, handed in here.
def test_a_played_caller_carries_its_own_rule_and_a_person_carries_none() -> None:
    ruled = a_call_from(
        _asked({"agent": AGENT, "persona": "homeowner"}),
        "clinica",
        PRODUCTION,
        AGENT,
        ("a price", None),
    )
    nobody = a_call_from(_asked({"agent": AGENT}), "clinica", PRODUCTION, AGENT)
    assert (ruled.accepts_when, ruled.declines_when) == ("a price", None)
    assert (nobody.accepts_when, nobody.declines_when) == (None, None)


class _QueryOnly:
    """A socket with nothing but its query string, which is all the context minting reads."""

    def __init__(self, params: dict[str, str]) -> None:
        self.query_params = params


def _asked(params: dict[str, str]) -> WebSocket:
    """That socket, as the minting's signature asks for it: nothing else of a WebSocket is read."""
    return cast(WebSocket, _QueryOnly(params))
