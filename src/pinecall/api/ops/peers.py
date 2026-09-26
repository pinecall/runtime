"""The other instance as a door here reaches it: production's sandbox, a sandbox's production."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

from pinecall.api.accounts.org_sso import the_http
from pinecall.api.deps import SettingsDep
from pinecall.auth.peers import Peer
from pinecall.types import PRODUCTION, SANDBOX


# Production asks its sandbox whose a ring is. Named by `PINECALL_SANDBOX_URL` and opened by the
# fleet key the sandbox minted for it; a production that names no sandbox — a box of one instance
# — has nobody to ask, and every ring is its own. The URL is in the instance's env file, which its
# worker, its overflow and its fleet loop read too, and the key is in the gateway's store alone:
# so the settings hold nobody to the pair, and the manifest refuses the deploy that would start a
# production naming a sandbox it holds no key of (infra/box/Makefile, `peers`).
def the_sandbox(connection: HTTPConnection, settings: SettingsDep) -> Peer | None:
    """The sandbox this production asks about a developer's phone, or None: it has none."""
    if settings.world != PRODUCTION or not settings.sandbox_url or not settings.sandbox_key:
        return None
    return Peer(the_http(connection), settings.sandbox_url, settings.sandbox_key)


# A sandbox asks production for the numbers the customers dial. Production is the identity the
# sandbox already names, so its URL is PINECALL_IDENTITY_URL and there is no second one to keep in
# step; the key is the fleet key production minted for it. Every row of a sandbox's own database
# is the sandbox's, so without that key there is nowhere to read a production number from.
def the_production(connection: HTTPConnection, settings: SettingsDep) -> Peer | None:
    """The production this sandbox reads numbers from, or None: it holds no key of it."""
    if settings.world != SANDBOX or not settings.identity_url or not settings.peer_key:
        return None
    return Peer(the_http(connection), settings.identity_url, settings.peer_key)


SandboxDep = Annotated["Peer | None", Depends(the_sandbox)]
ProductionDep = Annotated[Peer | None, Depends(the_production)]
