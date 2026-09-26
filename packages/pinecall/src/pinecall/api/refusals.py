"""The typed refusals a door answers with a status, mapped once so no endpoint writes a catch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from pinecall.accounts import (
    AlreadyAMember,
    LastAdmin,
    MirrorRefused,
    NobodyToSeat,
    NoSuchMember,
    NotActive,
    NotAMembersKey,
    ProviderRefused,
    ProviderUnreachable,
    SignsInWithProvider,
    WrongCredentials,
)
from pinecall.api.calls.supervise.aiming import VerbRefused
from pinecall.api.evals.runner import AlreadyRunning, NobodyServing
from pinecall.auth.identity import NotRedeemed
from pinecall.log.filters import FilterRefused
from pinecall.orgs.admission import QuotaExhausted
from pinecall.orgs.caller_codes import TooManyCodes
from pinecall.orgs.carriers import NoCarrier
from pinecall.orgs.outbound_guards import DialRefused
from pinecall.orgs.personas import NameTaken, NoSuchPersona
from pinecall.orgs.records import SlugTaken
from pinecall.orgs.tuning_store import VersionMoved
from pinecall.providers.embedder import EmbedderUnreachable, WrongModel, WrongWidth
from pinecall.providers.registry import NoProvider
from pinecall.providers.tts.vendor_voices import NotListed
from pinecall.routes.twilio import TwilioRefused
from pinecall.session.hold_melody import NotAHoldMelody
from pinecall.telephony import (
    CredentialsLost,
    DidNotDial,
    NobodyHolding,
    NoBoxCarrier,
    NoDomain,
    NoMediaPlane,
    NoneForSale,
    NoNumbers,
    NoOutboundHost,
    NoPhoneDoor,
    NotOnAccount,
    NotOurNumber,
    NoTrunk,
    NumberHeldElsewhere,
)
from pinecall.tokens import NoSuchCall, NotLive, TokenRefused
from pinecall.types import DeclarationRefused

# Every entry answers with the EXCEPTION'S OWN SENTENCE as the detail: a tenant who runs
# `pinecall knowledge push` with no embedder must read the vendor, the URL and the reason on
# their terminal, and a bare `Internal Server Error` is the afternoon this repo already lost.
#
# 400: the request itself is refused — a declaration the shapes will not take, a filter that
# parses to nothing, an upload that is not a melody. 401: nobody answers to what was presented — a
# password, a provider's word. 403: somebody does, and may not do this — invited, disabled, a
# machine's key. 404: what it names is not there. 409: the request was right and the rows disagree
# with it — a version that moved, a name held, vectors of another model or width, a run already in
# flight, the last admin. 429: the org's quota. 502: a carrier or an identity provider the box
# asked did not answer. 503: this box cannot honour it — no embedder answered, no vendor of that
# name has a key here.
#
# A door that answers one of these with ANOTHER status catches it itself and says why there: the
# personas and voices doors answer a refused declaration 422 (the body parsed and the words in it
# do not name a voice), the corner dependency 403, the managed-number door 503 naming the box's
# own variable.
STATUS_OF: dict[type[Exception], int] = {
    DeclarationRefused: 400,
    FilterRefused: 400,
    NotAHoldMelody: 400,
    NotOurNumber: 400,
    WrongCredentials: 401,
    SignsInWithProvider: 401,
    ProviderRefused: 401,
    NotActive: 403,
    NotAMembersKey: 403,
    NobodyToSeat: 403,
    NoSuchMember: 404,
    NoSuchCall: 404,
    NoSuchPersona: 404,
    NobodyServing: 404,
    NotListed: 404,
    NoPhoneDoor: 404,
    NoCarrier: 404,
    NotOnAccount: 404,
    NoneForSale: 404,
    VersionMoved: 409,
    NameTaken: 409,
    SlugTaken: 409,
    MirrorRefused: 409,
    LastAdmin: 409,
    AlreadyAMember: 409,
    NotLive: 409,
    TokenRefused: 409,
    NoTrunk: 409,
    NobodyHolding: 409,
    NoNumbers: 409,
    NoOutboundHost: 409,
    CredentialsLost: 409,
    NumberHeldElsewhere: 409,
    AlreadyRunning: 409,
    WrongWidth: 409,
    WrongModel: 409,
    QuotaExhausted: 429,
    TooManyCodes: 429,
    TwilioRefused: 502,
    DidNotDial: 502,
    ProviderUnreachable: 502,
    EmbedderUnreachable: 503,
    NoProvider: 503,
    NoMediaPlane: 503,
    NoDomain: 503,
    NoBoxCarrier: 503,
}


@runtime_checkable
class SaysItsStatus(Protocol):
    """A refusal carrying the status the door answers: a guard's, a desk verb's, production's."""

    @property
    def status(self) -> int: ...


# The three refusals whose status is decided where they are raised — which guard said no, which
# desk verb will not apply, what production answered — and travels with the sentence.
WITH_THEIR_OWN_STATUS: tuple[type[Exception], ...] = (DialRefused, VerbRefused, NotRedeemed)


# A lookup is NOT one of these doors: lookups/service.py catches whatever a lookup raised,
# writes `retrieval_skipped` or `memory_skipped` with that same sentence and lets the turn go on.
# A call never dies for a down embedder; a push, which has nothing to hand back, says so.
def refusals_answered_by(gateway: FastAPI) -> None:
    """Every door of this gateway answers these refusals with their status and their sentence."""
    for refusal, status in STATUS_OF.items():
        gateway.add_exception_handler(refusal, _under(status))
    for refusal in WITH_THEIR_OWN_STATUS:
        gateway.add_exception_handler(refusal, _under_its_own)


def _under(status: int) -> Callable[[Request, Exception], Response]:
    """One handler for one status: the sentence the refusal was raised with, as the detail."""

    def answered(_request: Request, refusal: Exception) -> Response:
        return JSONResponse({"detail": str(refusal)}, status_code=status)

    return answered


def _under_its_own(_request: Request, refusal: Exception) -> Response:
    """The status the refusal carries, and its sentence."""
    if not isinstance(refusal, SaysItsStatus):
        raise TypeError(f"{type(refusal).__name__} carries no status")
    return JSONResponse({"detail": str(refusal)}, status_code=refusal.status)
