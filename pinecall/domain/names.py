"""The words every part of the runtime speaks: worlds, channels, slugs, numbers, key scopes."""

import re
from typing import Literal

from pinecall.domain.errors import DeclarationRefused

type Json = str | int | float | bool | list[Json] | dict[str, Json] | None


type JsonObject = dict[str, Json]


# A lone secret, or the constructor's own keyword arguments (Azure's key and region, Google's
# service account, LiveKit's key pair).
type Credentials = str | JsonObject


# A key belongs to one environment; its agents, numbers and calls are isolated to it. A shared
# "staging" is a sandbox agent registered by a machine key (no holder), not a third environment.
type Env = Literal["production", "sandbox"]


# phone: SIP into a LiveKit room; web: the widget over WebRTC; whatsapp: text.
type Channel = Literal["phone", "web", "whatsapp"]


type Direction = Literal["inbound", "outbound"]


# How a number's row was written: bought by the box, imported from an account of the org, hooked
# by the org from its own carrier, or typed by the box's operator.
type RouteOrigin = Literal["bought", "imported", "hooked", "typed"]


# What one step of a call's way to an agent does now: works, waits on someone, or stops the call.
type StepState = Literal["ok", "waiting", "broken"]


# e.g. clinica-norte: an agent's slug, and an org's.
A_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


_E164 = re.compile(r"^\+[1-9]\d{1,14}$")


# Checked on every address before a letter is sent: whitespace would allow header injection
# (RFC 5322 §2.2).
AN_ADDRESS = re.compile(r"^[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+$")


ENVS: tuple[Env, ...] = ("production", "sandbox")


PRODUCTION: Env = "production"


SANDBOX: Env = "sandbox"


CHANNELS: tuple[Channel, ...] = ("phone", "web", "whatsapp")


CHANNELS_WITH_A_NUMBER: tuple[Channel, ...] = ("phone", "whatsapp")


THE_WIDGET: Channel = "web"


def other_world(world: Env) -> Env:
    """Return the world that is not this one."""
    return SANDBOX if world == PRODUCTION else PRODUCTION


def parse_env(word: str) -> Env:
    """Return the word as an Env, raising DeclarationRefused when it is neither world."""
    if word not in ENVS:
        raise DeclarationRefused(f"a key opens one of {sorted(ENVS)}, not {word!r}")
    return word


def parse_channel(word: str) -> Channel:
    """Return the word as a Channel, raising DeclarationRefused when it is none."""
    if word not in CHANNELS:
        raise DeclarationRefused(f"a call comes through one of {sorted(CHANNELS)}, not {word!r}")
    return word


def parse_slug(word: str) -> str:
    """Return the slug, raising DeclarationRefused unless it is lowercase words joined by dashes."""
    if not A_SLUG.match(word):
        raise DeclarationRefused(f"a slug is lowercase words joined by dashes, not {word!r}")
    return word


def dialable(number: str) -> bool:
    """Return whether the number is in E.164 form."""
    return _E164.match(number) is not None


def parse_e164(number: str) -> str:
    """Return the trimmed number, raising DeclarationRefused when it is not E.164."""
    data = number.strip()
    if not dialable(data):
        raise DeclarationRefused(
            f"a number is written in E.164 form, like +59829001199, not {data!r}"
        )
    return data
