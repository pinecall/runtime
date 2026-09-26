"""The fixtures about numbers: the two trunks, a Twilio that is a dict, and what a dial passes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pytest
from cryptography.fernet import Fernet

from pinecall.api.app import app
from pinecall.api.telephony import deps as placing
from pinecall.orgs.carriers import MemoryCarriers
from pinecall.orgs.dial_policies import MemoryDialling
from pinecall.orgs.outbound_credentials import MemoryOutboundTrunks
from pinecall.routes.twilio import Trunk, TwilioNumber, TwilioRefused
from pinecall.types import TwilioAccount
from tests.api.conftest import A_VAULT_KEY
from tests.routes.fakes import MemoryDispatches, MemoryOutbound, MemoryTrunks

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
    # What Twilio would sell, by country and area code: the box's account shops here.
    shelf: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    # The outbound half: the credential lists on the account, and which trunk carries each.
    logins: dict[str, str] = field(default_factory=dict[str, str])
    on_the_trunk: dict[str, set[str]] = field(default_factory=dict[str, set[str]])

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

    async def terminating(self, trunk_sid: str, domain: str) -> None:
        trunk = self.trunks[trunk_sid]
        self.trunks[trunk_sid] = Trunk(
            sid=trunk.sid, name=trunk.name, origination=trunk.origination, domain=domain
        )
        self.made.append(f"terminal {domain}")

    async def credential_list_named(self, name: str) -> str | None:
        return self.logins.get(name)

    async def create_credential_list(self, name: str, username: str, password: str) -> str:
        # The password is taken and never shown again, exactly as Twilio takes it: a fake that
        # handed it back would let a test pass that a repair on a real account cannot.
        assert password
        sid = f"CL_{len(self.logins) + 1}"
        self.logins[name] = sid
        self.made.append(f"login {name} as {username}")
        return sid

    async def credential_lists_on(self, trunk_sid: str) -> tuple[str, ...]:
        return tuple(sorted(self.on_the_trunk.get(trunk_sid, set())))

    async def with_credentials(self, trunk_sid: str, credential_list_sid: str) -> None:
        self.on_the_trunk.setdefault(trunk_sid, set()).add(credential_list_sid)
        self.made.append(f"trunked {credential_list_sid}")

    async def for_sale(self, country: str, area_code: str | None) -> str | None:
        on_the_shelf = self.shelf.get(f"{country} {area_code or ''}".strip(), [])
        return on_the_shelf[0] if on_the_shelf else None

    async def bought(self, number: str) -> TwilioNumber:
        for on_the_shelf in self.shelf.values():
            if number in on_the_shelf:
                on_the_shelf.remove(number)
        one = TwilioNumber(sid=f"PN_{len(self.owned) + 1}", number=number, name=number)
        self.owned.append(one)
        self.made.append(f"buy {number}")
        return one


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


@pytest.fixture
def outbound_trunks() -> MemoryOutboundTrunks:
    """The outbound trunks table, sealed under the suite's vault key, empty at the start."""
    return MemoryOutboundTrunks(Fernet(A_VAULT_KEY.encode()))


@pytest.fixture
def outbound() -> MemoryOutbound:
    """The SFU's outbound side as a dict: what a provisioning would have made."""
    return MemoryOutbound()


@pytest.fixture
def dispatches() -> MemoryDispatches:
    """Every job this gateway would have started, in order: what a dial actually dispatched."""
    return MemoryDispatches()


@pytest.fixture
def dialling() -> MemoryDialling:
    """The policy table and the ledger, one object: what each org may dial and what it has."""
    return MemoryDialling()


# Autouse, and it answers only the tests that build the app: `wired` in tests/api/conftest.py is
# one line under the file's 400-line ceiling, so the five deps the dial doors take are overridden
# from here instead. It asks for `wired` first, so these land on top of the overrides it set, and
# its own teardown clears every one of them.
#
# `dead_sentinel_keys` is asked for its ORDER and not for anything read here. A plugin's autouse
# fixture runs before a conftest's, so building the app from this one would have built it from the
# real environment — a `Settings()` with the box's own LiveKit URL in it, four tests deep in the
# suite and nowhere near this file. Asking for it puts it first, where it always was.
@pytest.fixture(autouse=True)
def the_placing_deps(request: pytest.FixtureRequest) -> Iterator[None]:
    """The dial doors' dependencies, answered from this test, for a test that has an app."""
    # A plugin is registered for every root pytest is given, `infra/tools/tests` among them, and
    # nothing there has an app or the suite's own fixtures. So this asks whether there is one
    # BEFORE it asks for anything, and a test without `wired` is left exactly as it was.
    if "wired" not in request.fixturenames:
        yield
        return
    request.getfixturevalue("dead_sentinel_keys")
    request.getfixturevalue("wired")
    trunks: MemoryOutboundTrunks = request.getfixturevalue("outbound_trunks")
    sfu: MemoryOutbound = request.getfixturevalue("outbound")
    jobs: MemoryDispatches = request.getfixturevalue("dispatches")
    both: MemoryDialling = request.getfixturevalue("dialling")
    app.dependency_overrides[placing.the_outbound_trunks] = lambda: trunks
    app.dependency_overrides[placing.the_outbound] = lambda: sfu
    app.dependency_overrides[placing.the_dispatches] = lambda: jobs
    app.dependency_overrides[placing.the_dial_policies] = lambda: both
    app.dependency_overrides[placing.the_dials] = lambda: both
    yield
