"""GET /.well-known/pinecall: what this gateway is, for a client that knows only its URL."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from pinecall._version import __version__
from pinecall.api.deps import SettingsDep
from pinecall.api.ops.box_settings import BoxSettingsDep
from pinecall.api.org.mail import OutboxDep
from pinecall.orgs.box_signin import GOOGLE, BoxSignIn
from pinecall_protocol import WireModel

router = APIRouter()


class Discovered(WireModel):
    """What a CLI or a page needs before it holds a key: which runtime, and what it does."""

    version: str
    # Which world this instance is (`PINECALL_WORLD`), and where the other one answers
    # (`PINECALL_ELSEWHERE_URL`, null when this instance was told of none): a CLI picks the URL
    # of the world it means off these, and a sign-in page points at the other before any key.
    world: str
    elsewhere: str | None = None
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
    # What this box is called and painted with (`GET /v1/ops/brand`): a sign-in page draws the
    # operator's name, logo and accent before anybody holds a key, so it rides here.
    brand: dict[str, Any] = Field(default_factory=dict[str, Any])
    # Whether the sign-in page may offer "Continue with Google": the operator wired a client at
    # `PUT /v1/ops/signin/google` and this box can open its secret. The button goes to
    # `GET /v1/login/google`.
    google: bool = False
    # Where this box's orgs pay (PINECALL_BILLING_URL), null on a box that bills nobody: a sign-up
    # page may say "free to start" and link the plans before anybody holds a key.
    billing_url: str | None = None


# No key at this door: it is how a client learns whether to offer a sign-up before anybody has one.
@router.get("/.well-known/pinecall")
async def discovery_answer(
    settings: SettingsDep, outbox: OutboxDep, box: BoxSettingsDep
) -> Discovered:
    """Which runtime and world, whether it is the cloud, whether a stranger may sign up, mail."""
    return Discovered(
        version=__version__,
        world=settings.world,
        elsewhere=settings.elsewhere_url,
        cloud=settings.cloud,
        signup=settings.signup,
        min_password=settings.min_password,
        # Off the outbox the process built once, so a malformed URL is said at startup, not here.
        mail=await outbox.the_box_can_send(),
        brand=(await outbox.brand()).as_json,
        google=await BoxSignIn(box).of(GOOGLE) is not None,
        billing_url=settings.billing_url,
    )
