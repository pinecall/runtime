"""Tests for the error hierarchy: every class carries its status and its sentence."""

import pytest

from pinecall.domain.errors import (
    AppRefused,
    Conflict,
    DeclarationRefused,
    GatewayRefused,
    MigrationsRefused,
    NotAllowed,
    NotAvailable,
    NotFound,
    NotSignedIn,
    PinecallError,
    QuotaExhausted,
    SettingsRefused,
    StoreUnreachable,
    UpstreamFailed,
)

STATUSES: list[tuple[type[PinecallError], int]] = [
    (PinecallError, 500),
    (DeclarationRefused, 400),
    (NotSignedIn, 401),
    (NotAllowed, 403),
    (NotFound, 404),
    (Conflict, 409),
    (QuotaExhausted, 429),
    (UpstreamFailed, 502),
    (GatewayRefused, 502),
    (NotAvailable, 503),
    (SettingsRefused, 500),
    (StoreUnreachable, 503),
    (MigrationsRefused, 500),
]


@pytest.mark.parametrize(("refusal", "status"), STATUSES, ids=[one.__name__ for one, _ in STATUSES])
def test_every_error_says_its_status_once(refusal: type[PinecallError], status: int) -> None:
    assert refusal.status == status
    assert issubclass(refusal, PinecallError)


def test_the_message_is_the_sentence_a_person_reads() -> None:
    refused = NotFound("no agent named clinica-norte in this org")
    assert str(refused) == "no agent named clinica-norte in this org"
    assert refused.status == 404


def test_the_base_is_what_a_door_catches_to_answer_any_of_them() -> None:
    with pytest.raises(PinecallError) as caught:
        raise Conflict("that slug is taken")
    assert caught.value.status == 409


def test_a_gateway_refusal_keeps_the_status_it_answered_and_none_when_unreachable() -> None:
    assert GatewayRefused("the call is sealed", answered=409).answered == 409
    assert GatewayRefused("nothing answered").answered is None


def test_an_apps_refusal_is_passed_on_with_its_own_status_and_sentence() -> None:
    refused = AppRefused(422, "the app has no tool named book")
    assert (refused.status, str(refused)) == (422, "the app has no tool named book")
