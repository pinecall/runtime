"""The typed refusals a door answers with a status, mapped once so no endpoint writes a catch."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from pinecall.providers.embedder import EmbedderUnreachable, WrongModel, WrongWidth

# 503: the request was right and this box cannot honour it — the embedder it was configured with
# did not answer, so nothing can be embedded and nothing searched. 409: the request was right and
# the rows it names disagree with it — vectors of another model, or of another width, which no
# index can compare. Both answer with the EXCEPTION'S OWN SENTENCE, which names the vendor, the
# URL and the reason: a tenant who runs `pinecall knowledge push` with no embedder must read that
# on their terminal, and a bare `Internal Server Error` is the afternoon this repo already lost.
STATUS_OF: dict[type[Exception], int] = {
    EmbedderUnreachable: 503,
    WrongWidth: 409,
    WrongModel: 409,
}


# A lookup is NOT one of these doors: lookups/service.py catches whatever a lookup raised,
# writes `retrieval_skipped` or `memory_skipped` with that same sentence and lets the turn go on.
# A call never dies for a down embedder; a push, which has nothing to hand back, says so.
def refusals_answered_by(gateway: FastAPI) -> None:
    """Every door of this gateway answers these refusals with their status and their sentence."""
    for refusal, status in STATUS_OF.items():
        gateway.add_exception_handler(refusal, _under(status))


def _under(status: int) -> Callable[[Request, Exception], Response]:
    """One handler for one status: the sentence the refusal was raised with, as the detail."""

    def answered(_request: Request, refusal: Exception) -> Response:
        return JSONResponse({"detail": str(refusal)}, status_code=status)

    return answered
