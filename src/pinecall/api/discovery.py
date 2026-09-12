"""GET /.well-known/pinecall: what this gateway is, for a client that knows only its URL."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall._version import __version__
from pinecall.api._deps import SettingsDep
from pinecall_protocol import WireModel

router = APIRouter()


class Discovered(WireModel):
    """What a CLI or a page needs before it holds a key: which runtime, and what it does."""

    version: str
    # True on Pinecall's own hosted gateway, where a plan is billed; False on a box somebody runs
    # themselves, where nothing of that exists. A setting, not a guess: PINECALL_CLOUD.
    cloud: bool
    # Whether `POST /v1/signup` answers here at all (PINECALL_SIGNUP). Its own fact, because a box
    # of its own may want sign-ups and a cloud may close them: the console draws the way in off
    # THIS, never off `cloud`.
    signup: bool = False


# No key at this door: it is how a client learns whether to offer a sign-up before anybody has one.
@router.get("/.well-known/pinecall")
async def discovered(settings: SettingsDep) -> Discovered:
    """Which runtime answers here, whether it is the cloud, and whether a stranger may sign up."""
    return Discovered(version=__version__, cloud=settings.cloud, signup=settings.signup)
