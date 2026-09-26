"""The /v1/ops door as the CLI knocks on it: one client, one key, one refusal a person can read."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Mapping
from types import TracebackType
from typing import Any, Self

import httpx

from pinecall._detail import refusal_detail
from pinecall._exceptions import PinecallError
from pinecall._settings import Settings, load_settings
from pinecall.types import DEFAULT_ORG

# The operator API answers off a table, never off a call: five seconds is a database that is down.
TIMEOUT_S = 5.0

# `routes rm` is answered with no body: there is nothing to say back about a row that is gone.
# The operator's door to the tenants: every verb about an org knocks under it.
OPS_ORGS = "/v1/ops/orgs"

NO_BODY = 204

NO_OPS_KEY = (
    "PINECALL_OPS_KEY is not set: the operator API is closed, and these verbs speak nothing else"
)


class OperatorRefused(PinecallError):
    """The gateway answered anything but yes. The message is what it said, for the terminal."""


class Operator:
    """Every /v1/ops door, as HTTP: the CLI groups name their own paths and share this client."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @classmethod
    def of(cls, settings: Settings) -> Self:
        """A client on this box's gateway, carrying the ops key. Refuses before it connects."""
        if not settings.ops_key:
            raise OperatorRefused(NO_OPS_KEY)
        return cls(
            httpx.AsyncClient(
                base_url=settings.gateway_url,
                headers={"Authorization": f"Bearer {settings.ops_key}"},
                timeout=TIMEOUT_S,
            )
        )

    async def __aenter__(self) -> Self:
        """`async with Operator.of(settings)` closes the client whatever the verb did."""
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        failure: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """A CLI that leaves a connection open is a CLI that hangs on exit."""
        await self._http.aclose()

    # The console is served by the same gateway at its root, so the gateway's own address is the
    # console's: what a verb prints when it hands a person a link to open in a browser.
    @property
    def base(self) -> str:
        """The gateway this client knocks at, with no trailing slash."""
        return str(self._http.base_url).rstrip("/")

    async def get(self, path: str, **params: str) -> Any:
        """A listing. Every door here names its org, so the parameters are always spelled out."""
        return self._read(await self._http.get(path, params=params))

    async def post(self, path: str, body: Mapping[str, Any] | None = None) -> Any:
        """A change. A door that answers 204 hands back None, which is what `rm` and friends do."""
        return self._read(await self._http.post(path, json=dict(body or {})))

    async def put(self, path: str, body: Mapping[str, Any]) -> Any:
        """A replacement, whole: what `orgs quota` sends."""
        return self._read(await self._http.put(path, json=dict(body)))

    async def delete(self, path: str, **params: str) -> None:
        """A row taken back. Nothing to read: the refusal is the whole answer when there is one."""
        self._read(await self._http.delete(path, params=params))

    def _read(self, answer: httpx.Response) -> Any:
        """The body, or the gateway's own sentence as a refusal a person can act on."""
        if answer.is_success:
            return None if answer.status_code == NO_BODY else answer.json()
        raise OperatorRefused(f"{answer.status_code}: {refusal_detail(answer.text.strip())}")


# The ops key is the BOX's, never an org's, so every door here names its org — and every verb
# that knocks on one takes the same flag, spelled once. An id or a slug: the door takes either.
def with_an_org(parser: argparse.ArgumentParser) -> None:
    """Whose rows this verb speaks about. A box with one org never has to say it."""
    parser.add_argument("--org", default=DEFAULT_ORG, help=f"by id or slug (default {DEFAULT_ORG})")


def against_the_gateway(verb: Callable[[Operator], Awaitable[int]]) -> int:
    """Open the operator API, run the verb, close it. A refusal is a sentence, not a traceback."""
    try:
        return asyncio.run(_with_an_operator(verb))
    except OperatorRefused as refused:
        print(refused, file=sys.stderr)
        return 1


async def _with_an_operator(verb: Callable[[Operator], Awaitable[int]]) -> int:
    async with Operator.of(load_settings()) as operator:
        return await verb(operator)
