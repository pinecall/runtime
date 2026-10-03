"""The line every channels test hooks numbers through: an org, a fake Twilio, Meta, an SFU."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from livekit import api

from pinecall.channels.telephony._twilio import verify
from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.process.connections import Connections, vault_of
from pinecall.tenancy import carriers, orgs, vault
from pinecall.tenancy.carriers import SipPeer, TwilioAccount
from pinecall.tenancy.dial_policy import Dial
from tests.conftest import settings_of
from tests.fakes.livekit import Server
from tests.fakes.meta import Graph, outside
from tests.fakes.twilio import Twilio

DOMAIN = "box.test"
HERE = "sip:box.test:5060;transport=udp"
A_NUMBER = "+15550100133"
# What a peer dials out with beside its username.
THE_OTHER_HALF = "the other half of the pair"
HER_PHONE = "+59899000001"
NFTABLES = Path(__file__).parents[2] / "infra/box/nftables.conf"


@dataclass
class Line:
    """The connections a test hooks numbers through, the org, and the fakes behind it."""

    connections: Connections
    org: str
    twilio: Twilio
    server: Server

    def scope(self, env: Env = "production") -> Scope:
        """The org's scope in the world."""
        return Scope(self.org, env)

    def account(self) -> TwilioAccount:
        """The fake account, as the org brings it."""
        return TwilioAccount(
            account_sid=self.twilio.account_sid, user=self.twilio.user, secret=self.twilio.secret
        )

    def rule(self, env: Env) -> api.SIPDispatchRuleInfo:
        """The org's rule of the world on the SFU."""
        return next(
            value
            for value in self.server.dialled.rules.values()
            if value.name == f"{self.org}:{env}"
        )

    def rules(self) -> list[str]:
        """The names of the rules on the SFU."""
        return sorted(value.name for value in self.server.dialled.rules.values())

    def trunk(self, name: str) -> api.SIPInboundTrunkInfo:
        """A trunk on the SFU by name."""
        return next(value for value in self.server.dialled.trunks.values() if value.name == name)


@pytest.fixture
async def line(pool: Pool, graph: Graph) -> AsyncIterator[Line]:
    """An org, a Twilio account nobody brought yet, Meta's Graph, an SFU with nothing on it."""
    org = await orgs.create(pool, "clinica", "Clinica")
    twilio = Twilio()
    server = Server()
    async with httpx.AsyncClient(transport=outside(twilio, graph)) as http:
        sealed = vault_of(Fernet.generate_key().decode())
        yield Line(
            Connections(
                settings=settings_of(DOMAIN),
                pool=pool,
                writing=pool,
                vault=sealed,
                http=http,
                server=server,
            ),
            org.id,
            twilio,
            server,
        )
    await server.aclose()


async def brought(line: Line) -> None:
    """The org brought its Twilio account, the pair tried first as the door does."""
    await verify(line.connections.http, line.account())
    await carriers.put_carrier(
        line.connections.pool, line.connections.vault, line.org, line.account()
    )


def a_peer(**said: object) -> SipPeer:
    """A PBX of the org's that calls from its office's network."""
    return SipPeer.model_validate(
        {
            "username": "pbx",
            "password": "a peer's password",
            "addresses": ["203.0.113.0/24"],
            **said,
        }
    )


def dial_of(line: Line, to: str = HER_PHONE, env: Env = "production", call: str = "call_1") -> Dial:
    """A dial of the org's agent to that number."""
    return Dial(line.scope(env), "recepcion", to, A_NUMBER, "m_ana", call, datetime.now(UTC))


async def ledger(line: Line) -> list[tuple[str, str | None, str | None]]:
    """Every dial written down: who asked, what refused it, the call it became."""
    async with line.connections.pool.connection() as connection:
        rows = await (
            await connection.execute("select asked_by, refused, call from dials order by id")
        ).fetchall()
    return [(row["asked_by"], row["refused"], row["call"]) for row in rows]


async def box_sells(line: Line, *numbers: str) -> None:
    """The box holds its own Twilio account, which has these numbers for sale."""
    box_twilio = line.account().model_dump(exclude={"kind", "label"})
    await vault.put_box_credentials(
        line.connections.pool, line.connections.vault, "twilio", box_twilio
    )
    line.twilio.for_sale = list(numbers)
