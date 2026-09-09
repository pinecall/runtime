"""The order between the two tables, both directions, and what the loser is told."""

import logging

import pytest

from pinecall.routes import answering
from pinecall.types import Route

pytestmark = pytest.mark.unit

NUMBER = "+59829000000"
ORG = "clinica"

TYPED = Route(org=ORG, agent="tienda-sur", channel="phone", number=NUMBER)
DECLARED = Route(org=ORG, agent="clinica-norte", channel="phone", number=NUMBER)
A_WIDGET = Route(org=ORG, agent="clinica-norte", channel="web")


def test_the_operators_row_answers_the_number_the_app_declared() -> None:
    """`routes add` moves a number with no deploy, so a declaration cannot take it back."""
    answered = answering.doors([TYPED], [DECLARED, A_WIDGET])
    assert [(door.route.agent, door.source) for door in answered] == [
        ("tienda-sur", "operator"),
        ("clinica-norte", "app"),
    ]


def test_a_declared_door_no_operator_typed_is_answered_by_the_app() -> None:
    """The other direction: with no row for it, the app's declaration is the whole answer."""
    answered = answering.doors([], [DECLARED])
    assert [(door.route.agent, door.source) for door in answered] == [("clinica-norte", "app")]


def test_the_app_that_loses_a_door_is_named_in_one_warning_beside_the_row_that_took_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Never a silent loss: the line names the number, the agent that answers, and the one that
    declared it too."""
    with caplog.at_level(logging.WARNING):
        answering.doors([TYPED], [DECLARED])
    assert NUMBER in caplog.text
    assert "tienda-sur" in caplog.text
    assert "clinica-norte" in caplog.text


def test_a_row_that_agrees_with_the_declaration_takes_nothing_and_says_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An operator who typed what the app already declared has taken no door from anybody."""
    agreeing = Route(org=ORG, agent="clinica-norte", channel="phone", number=NUMBER)
    with caplog.at_level(logging.WARNING):
        answered = answering.doors([agreeing], [DECLARED])
    assert [door.source for door in answered] == ["operator"]
    assert caplog.text == ""


def test_two_channels_at_one_number_are_two_doors() -> None:
    """A door is the channel and the number: a WhatsApp row leaves the phone declaration alone."""
    whatsapp = Route(org=ORG, agent="tienda-sur", channel="whatsapp", number=NUMBER)
    answered = answering.doors([whatsapp], [DECLARED])
    assert [door.route.channel for door in answered] == ["whatsapp", "phone"]
