"""Twilio, acted on for a tenant: its numbers listed, its trunk pointed at the box, one attached."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from pinecall._exceptions import PinecallError
from pinecall.types import TwilioAccount

TRUNKING_API = "https://trunking.twilio.com/v1"
ACCOUNTS_API = "https://api.twilio.com/2010-04-01"

# Twilio's signalling networks (twilio.com/docs/sip-trunking#ip-addresses-signaling), the ones a
# tenant's trunk sends its INVITEs from. The box's fence opens 5060 to exactly these
# (infra/box/nftables.conf, `carrier_signalling`), and tests/routes/test_twilio.py holds the two
# lists to be the same one: a network here the fence drops is a call that never rings.
TWILIO_SIGNALLING: tuple[str, ...] = (
    "54.172.60.0/30",
    "54.244.51.0/30",
    "54.171.127.192/30",
    "35.156.191.128/30",
    "54.65.63.192/30",
    "54.169.127.128/30",
    "54.252.254.64/30",
    "177.71.206.192/30",
)

# A trunk is read and written a handful of times per number and never on a call path.
TIMEOUT_S = 30.0

# The one origination URI a trunk of ours carries: the box, by name, on UDP 5060 — the port
# livekit-sip listens on and the fence opens (infra/box/sip.yaml, nftables.conf).
SIP_PORT = 5060
ORIGINATION_NAME = "pinecall-box"


class TwilioRefused(PinecallError):
    """Twilio answered anything but 2xx. The message carries what it said, for the door."""


@dataclass(frozen=True)
class TwilioNumber:
    """One number the account owns, as the console lists it to pick from."""

    sid: str
    number: str
    name: str


@dataclass(frozen=True)
class Trunk:
    """One SIP trunk on the account: its id, its name, and where it sends a call."""

    sid: str
    name: str
    origination: tuple[str, ...]


class TwilioApi(Protocol):
    """What the numbers door does on a tenant's account, and nothing more."""

    async def verified(self) -> str | None:
        """The account's own name when the credentials open it, or None when they do not."""
        ...

    async def numbers(self) -> tuple[TwilioNumber, ...]:
        """Every phone number the account owns."""
        ...

    async def trunk_named(self, name: str) -> Trunk | None:
        """The trunk by that name, or None: a trunk of ours is made once and found after."""
        ...

    async def create_trunk(self, name: str) -> Trunk:
        """A new trunk, empty."""
        ...

    async def pointed_at(self, trunk: Trunk, sip_uri: str) -> None:
        """The trunk's one origination URI set to the box: created, or the one standing replaced."""
        ...

    async def numbers_on(self, trunk_sid: str) -> tuple[str, ...]:
        """The numbers already attached to the trunk, E.164."""
        ...

    async def attached(self, trunk_sid: str, number_sid: str) -> None:
        """One number onto the trunk: from here the trunk owns its calls."""
        ...


# How the door reaches an account: built per request from the credentials the table decrypted,
# over the process's one httpx client. A test hands in a fake of the Protocol above instead.
type TwilioFor = Callable[[TwilioAccount], TwilioApi]


class HttpTwilio:
    """Twilio's REST API over httpx, on one account's credentials."""

    def __init__(self, http: httpx.AsyncClient, account: TwilioAccount) -> None:
        self._http = http
        self._account = account
        self._auth = httpx.BasicAuth(account.user, account.secret)

    async def verified(self) -> str | None:
        """GET the account itself: a 401 is the credentials being wrong, and nothing else is."""
        try:
            said = await self._get(f"{ACCOUNTS_API}/Accounts/{self._account.account_sid}.json")
        except TwilioRefused:
            return None
        return str(said.get("friendly_name") or self._account.account_sid)

    async def numbers(self) -> tuple[TwilioNumber, ...]:
        """One page of two hundred: an account with more numbers than that is not a tenant."""
        path = f"{ACCOUNTS_API}/Accounts/{self._account.account_sid}/IncomingPhoneNumbers.json"
        said = await self._get(f"{path}?PageSize=200")
        return tuple(
            TwilioNumber(
                sid=str(row["sid"]),
                number=str(row["phone_number"]),
                name=str(row.get("friendly_name") or row["phone_number"]),
            )
            for row in said.get("incoming_phone_numbers", [])
        )

    async def trunk_named(self, name: str) -> Trunk | None:
        """The trunk by friendly name, with the URIs it sends calls to."""
        said = await self._get(f"{TRUNKING_API}/Trunks?PageSize=50")
        for row in said.get("trunks", []):
            if row.get("friendly_name") == name:
                return await self._a_trunk(row)
        return None

    async def create_trunk(self, name: str) -> Trunk:
        """One POST; the account had no trunk of ours."""
        row = await self._post(f"{TRUNKING_API}/Trunks", {"FriendlyName": name})
        return Trunk(sid=str(row["sid"]), name=str(row["friendly_name"]), origination=())

    async def pointed_at(self, trunk: Trunk, sip_uri: str) -> None:
        """One origination URI, ours: made when the trunk carries none, replaced when it has one."""
        urls = f"{TRUNKING_API}/Trunks/{trunk.sid}/OriginationUrls"
        standing = await self._get(urls)
        rows: list[dict[str, Any]] = list(standing.get("origination_urls", []))
        form = {
            "FriendlyName": ORIGINATION_NAME,
            "SipUrl": sip_uri,
            "Weight": "10",
            "Priority": "10",
            "Enabled": "true",
        }
        if len(rows) > 1:
            raise TwilioRefused(
                f"trunk {trunk.name} carries {len(rows)} origination URIs: which is the box is a "
                "person's call, so nothing was moved"
            )
        where = urls if not rows else f"{urls}/{rows[0]['sid']}"
        await self._post(where, form)

    async def numbers_on(self, trunk_sid: str) -> tuple[str, ...]:
        """What the trunk already carries, so a number is attached once."""
        said = await self._get(f"{TRUNKING_API}/Trunks/{trunk_sid}/PhoneNumbers")
        return tuple(str(row["phone_number"]) for row in said.get("phone_numbers", []))

    async def attached(self, trunk_sid: str, number_sid: str) -> None:
        """The number onto the trunk. Its voice URL stops mattering: the trunk owns the call."""
        await self._post(
            f"{TRUNKING_API}/Trunks/{trunk_sid}/PhoneNumbers", {"PhoneNumberSid": number_sid}
        )

    async def _a_trunk(self, row: dict[str, Any]) -> Trunk:
        said = await self._get(f"{TRUNKING_API}/Trunks/{row['sid']}/OriginationUrls")
        return Trunk(
            sid=str(row["sid"]),
            name=str(row["friendly_name"]),
            origination=tuple(str(url["sip_url"]) for url in said.get("origination_urls", [])),
        )

    async def _get(self, url: str) -> dict[str, Any]:
        return await self._read(await self._http.get(url, auth=self._auth, timeout=TIMEOUT_S))

    async def _post(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        answer = await self._http.post(url, data=form, auth=self._auth, timeout=TIMEOUT_S)
        return await self._read(answer)

    @staticmethod
    async def _read(answer: httpx.Response) -> dict[str, Any]:
        """The resource, or Twilio's own sentence as a refusal the door hands on."""
        if answer.is_error:
            raise TwilioRefused(f"twilio {answer.status_code}: {answer.text[:300]}")
        read: Any = answer.json()
        return dict(read)


def origination_uri(domain: str) -> str:
    """Where a tenant's trunk sends the INVITE: the box's own name, its SIP port, UDP."""
    return f"sip:{domain}:{SIP_PORT};transport=udp"
