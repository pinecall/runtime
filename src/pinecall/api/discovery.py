"""GET /.well-known/pinecall: what this gateway is, for a client that knows only its URL."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall._version import __version__
from pinecall.api._deps import SettingsDep
from pinecall.api.org_mail import OutboxDep
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
    # How short a password this box accepts (PINECALL_MIN_PASSWORD). A page asking somebody to
    # choose one has to say the rule BEFORE they type, and it holds no key when it asks — so the
    # number rides here rather than being copied into the page, where it would drift the day an
    # operator moved it. 0 means there is no rule.
    min_password: int = 0
    # Whether the BOX can post a letter at all (PINECALL_SMTP_URL and PINECALL_MAIL_FROM). A
    # sign-in page reads it to know whether "Forgot your password?" may promise an email or has
    # to say "ask an admin of your org" — and it holds no key when it asks, so the fact rides
    # here. An org that wired its own mail can send where the box cannot; this says nothing
    # about that, because a page at the sign-in has not been told which org it is about yet.
    mail: bool = False


# No key at this door: it is how a client learns whether to offer a sign-up before anybody has one.
@router.get("/.well-known/pinecall")
async def discovered(settings: SettingsDep, outbox: OutboxDep) -> Discovered:
    """Which runtime, whether it is the cloud, whether a stranger may sign up, the floor, mail."""
    return Discovered(
        version=__version__,
        cloud=settings.cloud,
        signup=settings.signup,
        min_password=settings.min_password,
        # Off the outbox the process built once, so a malformed URL is said at startup, not here.
        mail=outbox.the_box_can_send,
    )
