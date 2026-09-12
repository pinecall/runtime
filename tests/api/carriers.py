"""The fixtures about numbers: the carriers table, the SFU's trunks, and a Twilio that is a dict."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from cryptography.fernet import Fernet

from pinecall.orgs.carriers import MemoryCarriers
from pinecall.routes.trunks import MemoryTrunks
from pinecall.routes.twilio import Trunk, TwilioNumber, TwilioRefused
from pinecall.types import TwilioAccount
from tests.api.conftest import A_VAULT_KEY

# Registered as a plugin by tests/conftest.py, beside tests/postgres.py and tests/api/people.py.

A_SID = "AC" + "0" * 32
A_KEY_SID = "SK" + "1" * 32
A_TWILIO = TwilioAccount(account_sid=A_SID, user=A_KEY_SID, secret="the-secret-nobody-sees")


@dataclass
class FakeTwilio:
    """One Twilio account as a dict: what it owns, what was made on it, and whether it opens."""

    account: TwilioAccount
    owned: list[TwilioNumber] = field(default_factory=list[TwilioNumber])
    trunks: dict[str, Trunk] = field(default_factory=dict[str, Trunk])
    on_trunk: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    opens: bool = True
    made: list[str] = field(default_factory=list[str])

    async def verified(self) -> str | None:
        return "Clínica Norte" if self.opens else None

    async def numbers(self) -> tuple[TwilioNumber, ...]:
        if not self.opens:
            raise TwilioRefused("twilio 401: Authenticate")
        return tuple(self.owned)

    async def trunk_named(self, name: str) -> Trunk | None:
        return next((trunk for trunk in self.trunks.values() if trunk.name == name), None)

    async def create_trunk(self, name: str) -> Trunk:
        trunk = Trunk(sid=f"TK_{len(self.trunks) + 1}", name=name, origination=())
        self.trunks[trunk.sid] = trunk
        self.made.append(f"trunk {name}")
        return trunk

    async def pointed_at(self, trunk: Trunk, sip_uri: str) -> None:
        self.trunks[trunk.sid] = Trunk(sid=trunk.sid, name=trunk.name, origination=(sip_uri,))
        self.made.append(f"origin {sip_uri}")

    async def numbers_on(self, trunk_sid: str) -> tuple[str, ...]:
        return tuple(sorted(self.on_trunk.get(trunk_sid, set())))

    async def attached(self, trunk_sid: str, number_sid: str) -> None:
        number = next(one.number for one in self.owned if one.sid == number_sid)
        self.on_trunk.setdefault(trunk_sid, set()).add(number)
        self.made.append(f"attach {number}")


@pytest.fixture
def twilio_account() -> FakeTwilio:
    """The clinic's Twilio: two numbers of its own, nothing wired yet."""
    return FakeTwilio(
        A_TWILIO,
        owned=[
            TwilioNumber(sid="PN_1", number="+14176743169", name="abai"),
            TwilioNumber(sid="PN_2", number="+34910000000", name="Madrid"),
        ],
    )


@pytest.fixture
def twilio(twilio_account: FakeTwilio) -> Callable[[TwilioAccount], FakeTwilio]:
    """How the door reaches an account: the one fake, whatever credentials it was handed."""
    return lambda _account: twilio_account


@pytest.fixture
def carriers() -> MemoryCarriers:
    """The carriers table, sealed under the suite's vault key, empty at the start of a test."""
    return MemoryCarriers(Fernet(A_VAULT_KEY.encode()))


@pytest.fixture
def trunks() -> MemoryTrunks:
    """The SFU's trunks as a dict: what an import would have admitted."""
    return MemoryTrunks()
