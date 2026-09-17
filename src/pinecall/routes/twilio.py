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

# The trunk on the BOX's own account, the one infra/tools/twilio_trunk.py wires for an operator
# and the one a number the box buys for a tenant is attached to. A tenant's trunk on its own
# account is `pinecall-<org>` (api/numbers.py); this one is the box's, and there is one.
BOX_TRUNK = "pinecall"

# What Twilio appends to a trunk's termination label to make the host the box dials. The label is
# unique across every Twilio account there is, which is why it carries the org and why a name
# somebody else already took comes back as Twilio's own refusal and not as a retry.
TERMINATION_SUFFIX = ".pstn.twilio.com"

# Twilio names an ISO country by two letters and looks for a local number a page at a time; one
# is what a plan buys, so one is what is asked for.
A_COUNTRY = 2


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
    """One SIP trunk on the account: its id, its name, and where calls go in either direction."""

    sid: str
    name: str
    # Where Twilio sends a call that ARRIVES at one of the trunk's numbers: the box.
    origination: tuple[str, ...]
    # The label of the trunk's Termination URI, `<domain>.pstn.twilio.com`, where the box sends a
    # call it PLACES. Empty until somebody sets one; Twilio holds the label unique across every
    # account it has, so a name already taken is refused in Twilio's own words.
    domain: str = ""


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

    async def for_sale(self, country: str, area_code: str | None) -> str | None:
        """One local, voice-capable number the account could buy there, E.164 — or none."""
        ...

    async def bought(self, number: str) -> TwilioNumber:
        """The number bought onto the account: this is the write that costs money."""
        ...

    # The four below are the OUTBOUND half of a trunk: where the box sends a call it places, and
    # what it authenticates as. Twilio takes either an IP access list or a credential list there;
    # this box brings credentials, because a box behind a changing address would otherwise stop
    # dialling the day its IP moved, silently.
    async def terminating(self, trunk_sid: str, domain: str) -> None:
        """The trunk's Termination URI label set, so <domain>.pstn.twilio.com takes our INVITE."""
        ...

    async def credential_list_named(self, name: str) -> str | None:
        """The credential list by that friendly name, or None: ours is made once and found after."""
        ...

    async def create_credential_list(self, name: str, username: str, password: str) -> str:
        """A credential list holding one credential. Twilio never shows the password again."""
        ...

    async def credential_lists_on(self, trunk_sid: str) -> tuple[str, ...]:
        """The credential lists already on the trunk, so ours is attached once."""
        ...

    async def with_credentials(self, trunk_sid: str, credential_list_sid: str) -> None:
        """The list onto the trunk: from here Twilio asks the box to authenticate as it."""
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

    async def for_sale(self, country: str, area_code: str | None) -> str | None:
        """Twilio's own search, one result: a number nobody holds, in that country and area."""
        path = f"{ACCOUNTS_API}/Accounts/{self._account.account_sid}/AvailablePhoneNumbers"
        query = "VoiceEnabled=true&PageSize=1"
        if area_code:
            query += f"&AreaCode={area_code}"
        said = await self._get(f"{path}/{country[:A_COUNTRY].upper()}/Local.json?{query}")
        rows: list[dict[str, Any]] = list(said.get("available_phone_numbers", []))
        return str(rows[0]["phone_number"]) if rows else None

    async def bought(self, number: str) -> TwilioNumber:
        """One POST to IncomingPhoneNumbers: the number is the account's from here."""
        path = f"{ACCOUNTS_API}/Accounts/{self._account.account_sid}/IncomingPhoneNumbers.json"
        row = await self._post(path, {"PhoneNumber": number})
        return TwilioNumber(
            sid=str(row["sid"]),
            number=str(row["phone_number"]),
            name=str(row.get("friendly_name") or row["phone_number"]),
        )

    async def terminating(self, trunk_sid: str, domain: str) -> None:
        """One POST on the trunk itself: the label is the whole of Twilio's termination setup."""
        # Twilio takes the whole host here and refuses a bare label (21245, "must end with
        # twilio.com"), and answers the whole host back: the label is ours, the suffix the wire's.
        await self._post(
            f"{TRUNKING_API}/Trunks/{trunk_sid}", {"DomainName": termination_host(domain)}
        )

    async def credential_list_named(self, name: str) -> str | None:
        """By friendly name, over one page: an account with fifty of these is not a tenant."""
        path = f"{ACCOUNTS_API}/Accounts/{self._account.account_sid}/SIP/CredentialLists.json"
        said = await self._get(f"{path}?PageSize=50")
        for row in said.get("credential_lists", []):
            if row.get("friendly_name") == name:
                return str(row["sid"])
        return None

    async def create_credential_list(self, name: str, username: str, password: str) -> str:
        """Two POSTs: the list, then the one credential in it. The password is never read back."""
        account = f"{ACCOUNTS_API}/Accounts/{self._account.account_sid}"
        row = await self._post(f"{account}/SIP/CredentialLists.json", {"FriendlyName": name})
        sid = str(row["sid"])
        await self._post(
            f"{account}/SIP/CredentialLists/{sid}/Credentials.json",
            {"Username": username, "Password": password},
        )
        return sid

    async def credential_lists_on(self, trunk_sid: str) -> tuple[str, ...]:
        """What the trunk already authenticates against, so ours is attached once."""
        said = await self._get(f"{TRUNKING_API}/Trunks/{trunk_sid}/CredentialLists")
        return tuple(str(row["sid"]) for row in said.get("credential_lists", []))

    async def with_credentials(self, trunk_sid: str, credential_list_sid: str) -> None:
        """The list onto the trunk. Twilio refuses a second one, and says so in its own words."""
        await self._post(
            f"{TRUNKING_API}/Trunks/{trunk_sid}/CredentialLists",
            {"CredentialListSid": credential_list_sid},
        )

    async def _a_trunk(self, row: dict[str, Any]) -> Trunk:
        said = await self._get(f"{TRUNKING_API}/Trunks/{row['sid']}/OriginationUrls")
        return Trunk(
            sid=str(row["sid"]),
            name=str(row["friendly_name"]),
            origination=tuple(str(url["sip_url"]) for url in said.get("origination_urls", [])),
            domain=str(row.get("domain_name") or "").removesuffix(TERMINATION_SUFFIX),
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


def termination_label(org: str) -> str:
    """The trunk's termination label for this org, in the only alphabet Twilio takes for one."""
    tidied = "".join(letter if letter.isalnum() else "-" for letter in org.lower())
    return f"{BOX_TRUNK}-{tidied.strip('-')}"


def termination_host(label: str) -> str:
    """Where the box sends a call it places through a Twilio trunk carrying that label."""
    return f"{label}{TERMINATION_SUFFIX}"
