"""GET /.well-known/pinecall: what this gateway is, for a client that knows only its URL."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall._version import __version__
from pinecall.api._deps import SettingsDep
from pinecall_protocol import WireModel

router = APIRouter()


class Discovered(WireModel):
    """The two facts a CLI or a page needs before it holds a key: which runtime, and whose."""

    version: str
    # True on Pinecall's own hosted gateway, where a stranger may sign up and a plan is billed;
    # False on a box somebody runs themselves, where nothing of that exists and the console shows
    # none of it. A setting, not a guess: PINECALL_CLOUD.
    cloud: bool


# No key at this door: it is how a client learns whether to offer a sign-up before anybody has one.
@router.get("/.well-known/pinecall")
async def discovered(settings: SettingsDep) -> Discovered:
    """Which runtime answers here, and whether it is Pinecall's cloud or a box of its own."""
    return Discovered(version=__version__, cloud=settings.cloud)
