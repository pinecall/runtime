"""Carrier accounts, numbers hooked at the carrier and admitted on the SFU, dialling out."""

import logging
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from ipaddress import ip_network
from typing import Annotated, Literal, Self

import httpx
from cryptography.fernet import MultiFernet
from livekit import api
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration
from livekit.protocol.sip import (
    CreateSIPDispatchRuleRequest,
    CreateSIPInboundTrunkRequest,
    DeleteSIPDispatchRuleRequest,
    DeleteSIPTrunkRequest,
    ListSIPDispatchRuleRequest,
    ListSIPInboundTrunkRequest,
    SIPDispatchRule,
    SIPDispatchRuleIndividual,
    SIPDispatchRuleInfo,
    SIPInboundTrunkInfo,
    SIPOutboundConfig,
    SIPTransport,
)
from psycopg.rows import DictRow
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from pinecall.channels import routes
from pinecall.channels.routes import Dialling, Dispatch
from pinecall.domain.errors import (
    Conflict,
    DeclarationRefused,
    NotAllowed,
    NotAvailable,
    NotFound,
    QuotaExhausted,
    UpstreamFailed,
)
from pinecall.domain.types import (
    CallContext,
    Channel,
    Corner,
    Env,
    Json,
    Route,
    new_call_id,
    parse_e164,
)
from pinecall.log.index import ever_reached
from pinecall.log.log import Logs, arrival_entry
from pinecall.log.store import Claim
from pinecall.postgres.pool import Pool
from pinecall.tenancy import admission, orgs, vault
from pinecall.wire.events import CallEnded
from pinecall.wire.rest import LegTrunk

logger = logging.getLogger(__name__)

# ── what the outside parties are ──

TRUNKING = "https://trunking.twilio.com/v1"
ACCOUNTS = "https://api.twilio.com/2010-04-01"
# Never on a call's path: an import or a purchase waits for Twilio, a call never does.
TIMEOUT_S = 30.0
# livekit-sip listens here (infra/box/sip.yaml), and nftables opens it to the carrier alone.
SIP_PORT = 5060
# Twilio's signalling edges (twilio.com/docs/sip-trunking/ip-addresses): the fence of every
# Twilio number on the SFU, and the set nftables.conf opens 5060 to. A test holds them equal.
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
# Termination hosts are global across Twilio; the label is `[a-z0-9-]`. Twilio wants the whole
# host in DomainName: a bare label is its error 21245.
TERMINATION_SUFFIX = ".pstn.twilio.com"
ROOM_PREFIX = "call-"
# The box's own Twilio account, sealed in box_settings as credentials/twilio.
THE_BOXS = "twilio"

# ── what a dial may reach ──

# E.164 country calling codes, matched longest first; no longer code starts with 1 or 7.
CALLING_CODES: frozenset[str] = frozenset(
    {
        "1", "7", "20", "27", "30", "31", "32", "33", "34", "36", "39", "40", "41", "43", "44",
        "45", "46", "47", "48", "49", "51", "52", "53", "54", "55", "56", "57", "58", "60",
        "61", "62", "63", "64", "65", "66", "81", "82", "84", "86", "90", "91", "92", "93",
        "94", "95", "98", "211", "212", "213", "216", "218", "220", "221", "222", "223", "224",
        "225", "226", "227", "228", "229", "230", "231", "232", "233", "234", "235", "236",
        "237", "238", "239", "240", "241", "242", "243", "244", "245", "246", "247", "248",
        "249", "250", "251", "252", "253", "254", "255", "256", "257", "258", "260", "261",
        "262", "263", "264", "265", "266", "267", "268", "269", "290", "291", "297", "298",
        "299", "350", "351", "352", "353", "354", "355", "356", "357", "358", "359", "370",
        "371", "372", "373", "374", "375", "376", "377", "378", "379", "380", "381", "382",
        "383", "385", "386", "387", "389", "420", "421", "423", "500", "501", "502", "503",
        "504", "505", "506", "507", "508", "509", "590", "591", "592", "593", "594", "595",
        "596", "597", "598", "599", "670", "672", "673", "674", "675", "676", "677", "678",
        "679", "680", "681", "682", "683", "685", "686", "687", "688", "689", "690", "691",
        "692", "800", "808", "850", "852", "853", "855", "856", "870", "878", "880", "881",
        "882", "883", "886", "888", "960", "961", "962", "963", "964", "965", "966", "967",
        "968", "970", "971", "972", "973", "974", "975", "976", "977", "979", "992", "993",
        "994", "995", "996", "998",
    }
)  # fmt: skip
# Satellite and global-service ranges: where international revenue-share fraud is dialled, and
# never a number that called anybody back.
NEVER_DIALLED = ("870", "878", "881", "882", "883", "888", "979")
# Shorter national numbers are short codes, service numbers or typos.
SHORTEST_NATIONAL = 5
# A call-back box, not a call centre: an operator raises them per org.
DIALS_A_MINUTE = 6
DIALS_A_DAY = 200
LONGEST_CALL_S = 600

# The guard that refused, written in the ledger and said in the sentence.
SHAPE = "shape"
STRANGER = "stranger"
TOO_FAST = "too_fast"
TOO_MANY = "too_many"

# ── sentences ──

NO_CARRIER = "the org has no carrier account: PUT /v1/carrier brings a Twilio account or a SIP peer"
NO_SUCH_ACCOUNT = "the org has no carrier account {account}"
WHICH_ACCOUNT = "the org has {count} carrier accounts ({accounts}): say which one with account"
TWILIO_REFUSED = "Twilio refused these credentials for {account}"
TWILIO_SAID = "Twilio: {said}"
NOT_ON_ACCOUNT = "{number} is not a number of Twilio account {account}"
ON_ANOTHER_TRUNK = (
    "{number} is on Twilio trunk {trunk} of {account}, pointed at {where}: moving it here takes it "
    "off there, so say move"
)
HELD_ELSEWHERE = (
    "{number} is on another org's trunk on this box ({trunk}): livekit-sip refuses an INVITE two "
    "trunks list, so nothing was written"
)
NO_DOMAIN = "this box has no PINECALL_DOMAIN: a carrier has nowhere to send a call"
NOT_A_PHONE = "a number is imported to answer phone or whatsapp, not {channel}"
KEY_WITHOUT_PEER = "a {kind} account takes no networks: they are the fence of a number you hook"
NO_ROUTE = "the org answers no {number} in the {env}"
NO_BOX_TWILIO = (
    "this box buys no numbers: it has no Twilio account of its own. Bring a carrier and import one"
)
NONE_FOR_SALE = "Twilio has no local voice number for sale in {country}{area}"
NOT_A_COUNTRY = "{number} starts with no country calling code E.164 assigns"
A_BILL = "{number} is +{code}, a satellite or global-service range this box never dials"
TOO_SHORT = "{number} is shorter than a number anybody calls from: {digits} digits after +{code}"
NOT_ONE_OF_OURS = (
    "{number} never called or wrote to this org in the {env}: a call back goes back to somebody "
    "({guard}); an operator lifts it with dial_anywhere"
)
A_BURST = "the org dialled {used} numbers in the last minute of its {limit} ({guard})"
A_DAYS_WORTH = "the org dialled {used} numbers today of its {limit} ({guard})"
NO_PHONE_DOOR = "agent {agent} answers at no phone number in the {env}: a call back shows one"
NOT_OUR_NUMBER = "{shown} is not a number agent {agent} answers at in the {env}"
DIALS_THROUGH_NOTHING = (
    "{shown} was hooked by the org or bought by the box: it dials through no account of the org"
)
NOT_PROVISIONED = (
    "the org's account {account} cannot place a call yet: POST /v1/carrier/outbound provisions it"
)
NO_OUTBOUND_HOST = (
    "SIP peer {account} declared no outbound_host: the networks a peer calls FROM are not an "
    "address it takes calls AT"
)
CREDENTIALS_LOST = (
    "Twilio account {account} holds the credential {username} on list {name}, whose password "
    "this box no longer has: delete that credential in Twilio's console and provision again"
)
DID_NOT_DIAL = "the media plane refused the call: {said}"
NOT_A_NETWORK = "{address} is not a network a peer calls from, like 203.0.113.0/24"
HALF_A_PAIR = "outbound_username is given without outbound_password: a peer is dialled with both"


# ── the accounts ──

type Transport = Literal["auto", "udp", "tcp", "tls"]
TRANSPORTS: dict[Transport, SIPTransport] = {
    "auto": SIPTransport.SIP_TRANSPORT_AUTO,
    "udp": SIPTransport.SIP_TRANSPORT_UDP,
    "tcp": SIPTransport.SIP_TRANSPORT_TCP,
    "tls": SIPTransport.SIP_TRANSPORT_TLS,
}
A_SID = r"^(AC|SK)[0-9a-fA-F]{32}$"
AN_ACCOUNT_SID = r"^AC[0-9a-fA-F]{32}$"


class TwilioAccount(BaseModel):
    """A Twilio account: its SID, and an API key SID with its secret (or the SID and auth token)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["twilio"] = "twilio"
    account_sid: Annotated[str, Field(pattern=AN_ACCOUNT_SID)]
    user: Annotated[str, Field(pattern=A_SID)]
    secret: Annotated[str, Field(min_length=1)]
    label: str = ""


class SipPeer(BaseModel):
    """A PBX or carrier that speaks SIP: where it calls from, its pair, where it is dialled."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["sip"] = "sip"
    username: Annotated[str, Field(min_length=1)]
    password: Annotated[str, Field(min_length=1)]
    addresses: Annotated[list[str], Field(min_length=1)]
    outbound_host: str | None = None
    outbound_transport: Transport = "auto"
    outbound_username: str | None = None
    outbound_password: str | None = None
    label: str = ""

    @field_validator("addresses")
    @classmethod
    def _networks(cls, addresses: list[str]) -> list[str]:
        for one in addresses:
            try:
                ip_network(one, strict=False)
            except ValueError:
                raise ValueError(NOT_A_NETWORK.format(address=one)) from None
        return addresses

    # One account in both directions is what most carriers sell: unsaid, the inbound pair dials.
    @model_validator(mode="after")
    def _a_whole_pair(self) -> Self:
        if self.outbound_username and not self.outbound_password:
            raise ValueError(HALF_A_PAIR)
        return self


class WhatsappAccount(BaseModel):
    """A WhatsApp number at Meta: the id replies are sent from, and the org's token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["whatsapp"] = "whatsapp"
    phone_number_id: Annotated[str, Field(min_length=1)]
    access_token: Annotated[str, Field(min_length=1)]
    label: str = ""


type Account = Annotated[TwilioAccount | SipPeer | WhatsappAccount, Field(discriminator="kind")]
ACCOUNT: TypeAdapter[TwilioAccount | SipPeer | WhatsappAccount] = TypeAdapter(Account)


class Termination(BaseModel):
    """How the box dials out through a Twilio account: the host and the credential it made."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str
    username: str
    password: str


class _Sealed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account: Account
    outbound: Termination | None = None


@dataclass(frozen=True)
class Carrier:
    """One account of the org at an outside party that holds its numbers."""

    org: str
    account: TwilioAccount | SipPeer | WhatsappAccount
    # Twilio only, written by provisioning: Twilio shows a credential's password once.
    outbound: Termination | None = None

    @property
    def id(self) -> str:
        """The account's own id: Twilio's SID, the peer's username, Meta's phone number id."""
        match self.account:
            case TwilioAccount():
                return self.account.account_sid
            case SipPeer():
                return self.account.username
            case WhatsappAccount():
                return self.account.phone_number_id


@dataclass(frozen=True)
class Exchange:
    """What hooking a number reaches: tables, vault, the carriers' HTTP, the SFU, the name."""

    pool: Pool
    vault: MultiFernet
    http: httpx.AsyncClient
    server: api.LiveKitAPI
    # PINECALL_DOMAIN: where a carrier sends a call.
    domain: str | None


CARRIERS = (
    "SELECT kind, account, ciphertext FROM carriers WHERE org = %(org)s ORDER BY set_at, account"
)
PUT_CARRIER = """
INSERT INTO carriers (org, kind, account, label, ciphertext)
VALUES (%(org)s, %(kind)s, %(account)s, %(label)s, %(ciphertext)s)
ON CONFLICT (org, account) DO UPDATE SET kind = excluded.kind, label = excluded.label,
    ciphertext = excluded.ciphertext, set_at = now()
"""
DROP_CARRIER = (
    "DELETE FROM carriers WHERE org = %(org)s AND account = %(account)s RETURNING account"
)


async def carriers_of(pool: Pool, sealed: MultiFernet, org: str) -> list[Carrier]:
    """The org's carrier accounts, oldest first, opened; one sealed under a lost key is skipped."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(CARRIERS, {"org": org})).fetchall()
    return [one for one in (_opened(sealed, org, row) for row in rows) if one is not None]


async def carrier(pool: Pool, sealed: MultiFernet, org: str, account: str | None = None) -> Carrier:
    """The account named, or the org's only one; NotFound or Conflict otherwise."""
    held = await carriers_of(pool, sealed, org)
    if account is not None:
        found = next((one for one in held if one.id == account), None)
        if found is None:
            raise NotFound(NO_SUCH_ACCOUNT.format(account=account))
        return found
    if not held:
        raise NotFound(NO_CARRIER)
    if len(held) > 1:
        named = ", ".join(one.id for one in held)
        raise Conflict(WHICH_ACCOUNT.format(count=len(held), accounts=named))
    return held[0]


async def bring(
    exchange: Exchange, org: str, account: TwilioAccount | SipPeer | WhatsappAccount
) -> Carrier:
    """Keep the account, a Twilio one verified first; bringing it again replaces its secret."""
    if isinstance(account, TwilioAccount) and not await Twilio(exchange.http, account).verified():
        raise DeclarationRefused(TWILIO_REFUSED.format(account=account.account_sid))
    kept = Carrier(org=org, account=account)
    was = next(
        (one for one in await carriers_of(exchange.pool, exchange.vault, org) if one.id == kept.id),
        None,
    )
    # The termination stays with the account it was made on: only a Twilio account holds one.
    if was is not None and was.account.kind == account.kind:
        kept = replace(kept, outbound=was.outbound)
    await _kept(exchange.pool, exchange.vault, kept)
    return kept


async def take_back(pool: Pool, sealed: MultiFernet, org: str, account: str | None = None) -> str:
    """Forget the account; its numbers stay routed until each is let go."""
    gone = await carrier(pool, sealed, org, account)
    async with pool.connection() as connection:
        await connection.execute(DROP_CARRIER, {"org": org, "account": gone.id})
    return gone.id


@dataclass(frozen=True)
class Owned:
    """A number one of the org's accounts owns, and whether this world already imported it."""

    number: str
    name: str
    imported: bool
    account: str


# A SIP peer owns what it owns and nobody here can list it: its import takes the number typed.
async def available(
    exchange: Exchange, corner: Corner, account: str | None = None
) -> tuple[str, list[Owned]]:
    """The kind of the org's accounts, and every number its Twilio accounts own."""
    if account is None:
        held = await carriers_of(exchange.pool, exchange.vault, corner.org)
    else:
        held = [await carrier(exchange.pool, exchange.vault, corner.org, account)]
    if not held:
        raise NotFound(NO_CARRIER)
    imported = {one.number for one in await routes.of_org(exchange.pool, corner.org, corner.env)}
    twilios = [one.account for one in held if isinstance(one.account, TwilioAccount)]
    owned = [
        Owned(
            number=number.phone_number,
            name=number.friendly_name or number.phone_number,
            imported=number.phone_number in imported,
            account=twilio.account_sid,
        )
        for twilio in twilios
        for number in await Twilio(exchange.http, twilio).numbers()
    ]
    return ("twilio" if twilios else held[0].account.kind), owned


# ── Twilio's REST, by hand ──


class TwilioNumber(BaseModel):
    """A number an account owns, and the trunk it is attached to, if any."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    sid: str
    phone_number: str
    friendly_name: str = ""
    trunk_sid: str | None = None


class TwilioTrunk(BaseModel):
    """A SIP trunk of an account."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    sid: str
    friendly_name: str = ""
    domain_name: str | None = None


class _Origination(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    sid: str
    sip_url: str


class _Listed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    sid: str
    friendly_name: str = ""


class _Credential(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    username: str


class _ForSale(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    phone_number: str


class _Meta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    next_page_url: str | None = None


# The Accounts API names the next page by a path, the Trunking API by a whole URL in `meta`.
class _Page(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    next_page_uri: str | None = None
    meta: _Meta | None = None


class _Refusal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    message: str = ""


_OBJECT: TypeAdapter[dict[str, Json]] = TypeAdapter(dict[str, Json])
_NUMBERS: TypeAdapter[list[TwilioNumber]] = TypeAdapter(list[TwilioNumber])
_TRUNKS: TypeAdapter[list[TwilioTrunk]] = TypeAdapter(list[TwilioTrunk])
_ORIGINATIONS: TypeAdapter[list[_Origination]] = TypeAdapter(list[_Origination])
_LISTS: TypeAdapter[list[_Listed]] = TypeAdapter(list[_Listed])
_FOR_SALE: TypeAdapter[list[_ForSale]] = TypeAdapter(list[_ForSale])
_CREDENTIALS: TypeAdapter[list[_Credential]] = TypeAdapter(list[_Credential])


class Twilio:
    """One Twilio account's REST API, over the box's HTTP client."""

    def __init__(self, http: httpx.AsyncClient, account: TwilioAccount) -> None:
        """The account and the pair it is asked with; nothing is asked yet."""
        self.http = http
        self.sid = account.account_sid
        self.auth = httpx.BasicAuth(account.user, account.secret)
        self.home = f"{ACCOUNTS}/Accounts/{account.account_sid}"

    async def verified(self) -> bool:
        """Whether the pair opens the account."""
        answer = await self.http.get(f"{self.home}.json", auth=self.auth, timeout=TIMEOUT_S)
        if answer.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            return False
        _said(answer)
        return True

    async def numbers(self) -> list[TwilioNumber]:
        """Every number the account owns, every page."""
        listed = await self._every(
            f"{self.home}/IncomingPhoneNumbers.json?PageSize=200", "incoming_phone_numbers"
        )
        return _NUMBERS.validate_python(listed)

    async def number(self, number: str) -> TwilioNumber | None:
        """The account's number, or None when it owns no such number."""
        said = await self._get(f"{self.home}/IncomingPhoneNumbers.json", {"PhoneNumber": number})
        found = _NUMBERS.validate_python(said.get("incoming_phone_numbers", []))
        return found[0] if found else None

    async def trunk_pointing_at(self, uri: str) -> TwilioTrunk | None:
        """The account's trunk whose origination is this URI, whatever it is named."""
        trunks = _TRUNKS.validate_python(
            await self._every(f"{TRUNKING}/Trunks?PageSize=50", "trunks")
        )
        for trunk in trunks:
            if uri in {one.sip_url for one in await self._originations(trunk.sid)}:
                return trunk
        return None

    async def trunk(self, sid: str) -> TwilioTrunk:
        """One trunk of the account."""
        return TwilioTrunk.model_validate(await self._get(f"{TRUNKING}/Trunks/{sid}"))

    async def originations(self, sid: str) -> list[str]:
        """Where the trunk sends a call."""
        return [one.sip_url for one in await self._originations(sid)]

    async def trunk_made(self, name: str, uri: str) -> TwilioTrunk:
        """A new trunk, sending its calls to the URI."""
        made = TwilioTrunk.model_validate(
            await self._post(f"{TRUNKING}/Trunks", {"FriendlyName": name})
        )
        form = {
            "FriendlyName": name,
            "SipUrl": uri,
            "Weight": "10",
            "Priority": "10",
            "Enabled": "true",
        }
        await self._post(f"{TRUNKING}/Trunks/{made.sid}/OriginationUrls", form)
        return made

    async def attach(self, trunk: str, number: str) -> None:
        """Attach the number to the trunk: its voice URL is then ignored."""
        await self._post(f"{TRUNKING}/Trunks/{trunk}/PhoneNumbers", {"PhoneNumberSid": number})

    async def detach(self, trunk: str, number: str) -> None:
        """Take the number off a trunk."""
        answer = await self.http.delete(
            f"{TRUNKING}/Trunks/{trunk}/PhoneNumbers/{number}", auth=self.auth, timeout=TIMEOUT_S
        )
        if answer.is_error:
            _said(answer)

    async def for_sale(self, country: str, area_code: str | None) -> str | None:
        """One local voice number on sale in the country, or None."""
        asked = {"VoiceEnabled": "true", "PageSize": "1"} | (
            {"AreaCode": area_code} if area_code else {}
        )
        said = await self._get(
            f"{self.home}/AvailablePhoneNumbers/{country.upper()}/Local.json", asked
        )
        found = _FOR_SALE.validate_python(said.get("available_phone_numbers", []))
        return found[0].phone_number if found else None

    async def bought(self, number: str) -> TwilioNumber:
        """Buy the number onto the account: this costs money."""
        said = await self._post(f"{self.home}/IncomingPhoneNumbers.json", {"PhoneNumber": number})
        return TwilioNumber.model_validate(said)

    async def terminated(self, trunk: str, host: str) -> None:
        """Set where the box dials the trunk: <label>.pstn.twilio.com, the whole host."""
        await self._post(f"{TRUNKING}/Trunks/{trunk}", {"DomainName": host})

    async def credential_list(self, name: str) -> str | None:
        """The account's credential list by name, or None."""
        listed = _LISTS.validate_python(
            await self._every(
                f"{self.home}/SIP/CredentialLists.json?PageSize=50", "credential_lists"
            )
        )
        return next((one.sid for one in listed if one.friendly_name == name), None)

    async def credential_list_made(self, name: str) -> str:
        """A new, empty credential list."""
        made = _Listed.model_validate(
            await self._post(f"{self.home}/SIP/CredentialLists.json", {"FriendlyName": name})
        )
        return made.sid

    async def usernames_in(self, credential_list: str) -> list[str]:
        """The usernames a credential list holds; never a password, which Twilio never shows."""
        listed = await self._every(
            f"{self.home}/SIP/CredentialLists/{credential_list}/Credentials.json", "credentials"
        )
        return [one.username for one in _CREDENTIALS.validate_python(listed)]

    async def credential_added(self, credential_list: str, username: str, password: str) -> None:
        """One more credential on the list; Twilio shows its password this once."""
        form = {"Username": username, "Password": password}
        await self._post(
            f"{self.home}/SIP/CredentialLists/{credential_list}/Credentials.json", form
        )

    async def lists_on(self, trunk: str) -> list[str]:
        """The credential lists the trunk asks the box for."""
        listed = await self._every(f"{TRUNKING}/Trunks/{trunk}/CredentialLists", "credential_lists")
        return [one.sid for one in _LISTS.validate_python(listed)]

    async def list_attached(self, trunk: str, credential_list: str) -> None:
        """Have the trunk ask the box for this credential."""
        form = {"CredentialListSid": credential_list}
        await self._post(f"{TRUNKING}/Trunks/{trunk}/CredentialLists", form)

    async def _originations(self, trunk: str) -> list[_Origination]:
        listed = await self._every(f"{TRUNKING}/Trunks/{trunk}/OriginationUrls", "origination_urls")
        return _ORIGINATIONS.validate_python(listed)

    # A listing past its first page silently lost the rest in v1; every page is read.
    async def _every(self, url: str, key: str) -> list[Json]:
        found: list[Json] = []
        following: str | None = url
        while following is not None:
            said = await self._get(following)
            listed = said.get(key)
            found += listed if isinstance(listed, list) else []
            page = _Page.model_validate(said)
            path = page.next_page_uri
            following = (
                f"https://{httpx.URL(url).host}{path}"
                if path
                else (page.meta.next_page_url if page.meta else None)
            )
        return found

    async def _get(self, url: str, asked: dict[str, str] | None = None) -> dict[str, Json]:
        answer = await self.http.get(url, params=asked, auth=self.auth, timeout=TIMEOUT_S)
        return _said(answer)

    async def _post(self, url: str, form: dict[str, str]) -> dict[str, Json]:
        answer = await self.http.post(url, data=form, auth=self.auth, timeout=TIMEOUT_S)
        return _said(answer)


# Twilio's own sentence reaches the person; the pair never does.
def _said(answer: httpx.Response) -> dict[str, Json]:
    if answer.is_error:
        try:
            said = _Refusal.model_validate_json(answer.content).message
        except ValidationError:
            said = f"{answer.status_code}"
        raise UpstreamFailed(TWILIO_SAID.format(said=said or answer.status_code))
    return _OBJECT.validate_json(answer.content) if answer.content else {}


def origination_uri(domain: str) -> str:
    """The SIP URI a Twilio trunk sends this box's calls to."""
    return f"sip:{domain}:{SIP_PORT};transport=udp"


def termination_host(domain: str, account: str) -> str:
    """The host the box dials an account through: one per account per box, `[a-z0-9-]`."""
    named = f"{domain}-{account[-8:]}".lower()
    label = "".join(letter if letter.isalnum() else "-" for letter in named).strip("-")
    return f"{label}{TERMINATION_SUFFIX}"


# ── a number hooked: at the carrier, on the SFU, in the table ──


@dataclass(frozen=True)
class Import:
    """A number wanted: where it answers, and how it reaches the box."""

    corner: Corner
    agent: str
    number: str
    channel: Channel = "phone"
    # The account it lives in; unsaid, the org's only one.
    account: str | None = None
    # The org points the number here itself: no account is touched.
    hooked: bool = False
    # A self-hooked number's own fence; unsaid, Twilio's.
    networks: tuple[str, ...] = ()
    # Take the number off another trunk of its account first.
    move: bool = False


@dataclass(frozen=True)
class Purchase:
    """A number wanted from the box's own account."""

    corner: Corner
    agent: str
    country: str
    area_code: str | None = None
    channel: Channel = "phone"


@dataclass(frozen=True)
class Fence:
    """Who the SFU admits a number's INVITE from: one trunk per fence."""

    trunk: str
    networks: tuple[str, ...]
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class AtTwilio:
    """The number at its Twilio account, and the trunk that points it here."""

    twilio: Twilio
    number: TwilioNumber | None
    trunk: TwilioTrunk | None
    # Bought in this very request: a dry run names it and buys nothing.
    for_sale: str | None = None


@dataclass
class Looked:
    """Everything a hook needs, read before anything is written."""

    route: Route
    fence: Fence | None
    account: str | None
    networks: tuple[str, ...]
    fleet: str
    domain: str
    at_twilio: AtTwilio | None = None
    # The org's own other trunks that list the number and must let it go.
    leaving: list[SIPInboundTrunkInfo] = field(default_factory=list[SIPInboundTrunkInfo])
    trunk: SIPInboundTrunkInfo | None = None
    rule: SIPDispatchRuleInfo | None = None
    other_rule: SIPDispatchRuleInfo | None = None
    routed: Route | None = None


@dataclass(frozen=True)
class Plan:
    """What a hook does, one sentence a step, and the route it ends in."""

    route: Route
    steps: list[str]
    dry_run: bool


async def plan_import(exchange: Exchange, wanted: Import) -> Plan:
    """What importing the number would do, writing nothing."""
    looked = await _looked_for_import(exchange, wanted)
    return Plan(route=looked.route, steps=_steps(looked, done=False), dry_run=True)


async def import_number(exchange: Exchange, wanted: Import) -> Plan:
    """Hook the number at its account, admit it on the SFU in its world, route it."""
    looked = await _looked_for_import(exchange, wanted)
    steps = _steps(looked, done=True)
    await _hooked(exchange, looked)
    return Plan(route=looked.route, steps=steps, dry_run=False)


async def plan_buy(exchange: Exchange, wanted: Purchase) -> Plan:
    """What buying would do: the number Twilio would sell, and nothing bought."""
    looked = await _looked_for_purchase(exchange, wanted)
    return Plan(route=looked.route, steps=_steps(looked, done=False), dry_run=True)


async def buy_number(exchange: Exchange, wanted: Purchase) -> Plan:
    """Buy a number on the box's account and hook it as an import, counted as the box's."""
    looked = await _looked_for_purchase(exchange, wanted)
    steps = _steps(looked, done=True)
    at = looked.at_twilio
    if at is not None and at.for_sale is not None:
        looked.at_twilio = replace(at, number=await at.twilio.bought(at.for_sale), for_sale=None)
    await _hooked(exchange, looked)
    return Plan(route=looked.route, steps=steps, dry_run=False)


async def release(exchange: Exchange, org: str, number: str, env: Env) -> None:
    """Let the number go: the route, the SFU's admission, its world's rule; the account keeps it."""
    route = await routes.of_number(exchange.pool, org, number)
    if route is None or route.env != env:
        raise NotFound(NO_ROUTE.format(number=number, env=env))
    await routes.remove(exchange.pool, org, number)
    for trunk in await _trunks_listing(exchange.server, number):
        if _is_the_orgs(trunk.name, org):
            kept = [one for one in trunk.numbers if one != number]
            await _numbered(exchange.server, org, trunk, kept)
    rule = await _rule_named(exchange.server, _rule_name(org, env))
    if rule is not None:
        await _rule_numbered(exchange.server, rule, [one for one in rule.numbers if one != number])


async def move(exchange: Exchange, org: str, number: str, env: Env) -> Route:
    """Move the number into the other world: its row and the two rules, the trunk untouched."""
    route = await routes.of_number(exchange.pool, org, number)
    if route is None:
        raise NotFound(NO_ROUTE.format(number=number, env="either world"))
    if route.env == env:
        return route
    # A WhatsApp number is on no trunk: its world is its row alone.
    if route.channel != "phone":
        await routes.moved(exchange.pool, org, number, env)
        return replace(route, env=env)
    fleets = await orgs.fleets(exchange.pool)
    trunks = [
        one.sip_trunk_id
        for one in await _trunks_listing(exchange.server, number)
        if _is_the_orgs(one.name, org)
    ]
    left = await _rule_named(exchange.server, _rule_name(org, route.env))
    if left is not None:
        await _rule_numbered(exchange.server, left, [one for one in left.numbers if one != number])
    await _ruled(exchange.server, World(org, env, orgs.fleet_of(fleets, env)), trunks, number)
    await routes.moved(exchange.pool, org, number, env)
    return replace(route, env=env)


async def _looked_for_import(exchange: Exchange, wanted: Import) -> Looked:
    number = parse_e164(wanted.number)
    if wanted.channel not in {"phone", "whatsapp"}:
        raise DeclarationRefused(NOT_A_PHONE.format(channel=wanted.channel))
    domain = _domain(exchange)
    org, env = wanted.corner.org, wanted.corner.env
    route = Route(org=org, agent=wanted.agent, channel=wanted.channel, number=number, env=env)
    fleets = await orgs.fleets(exchange.pool)
    held = (
        None if wanted.hooked else await carrier(exchange.pool, exchange.vault, org, wanted.account)
    )
    if held is not None and wanted.networks:
        raise DeclarationRefused(KEY_WITHOUT_PEER.format(kind=held.account.kind))
    looked = Looked(
        route=route,
        fence=_fence(org, number, held, wanted.networks) if wanted.channel == "phone" else None,
        account=None if held is None else held.id,
        networks=wanted.networks,
        fleet=orgs.fleet_of(fleets, env),
        domain=domain,
    )
    if held is not None and isinstance(held.account, TwilioAccount) and wanted.channel == "phone":
        looked.at_twilio = await _at_twilio(exchange, held.account, number, move=wanted.move)
    return await _looked_on_the_sfu(exchange, looked)


async def _looked_for_purchase(exchange: Exchange, wanted: Purchase) -> Looked:
    domain = _domain(exchange)
    org, env = wanted.corner.org, wanted.corner.env
    boxs = await _the_boxs_twilio(exchange)
    await admission.admit_number(
        exchange.pool, org, env, bought=await routes.managed_in(exchange.pool, org, env)
    )
    twilio = Twilio(exchange.http, boxs)
    for_sale = await twilio.for_sale(wanted.country, wanted.area_code)
    if for_sale is None:
        area = f", area code {wanted.area_code}" if wanted.area_code else ""
        raise NotFound(NONE_FOR_SALE.format(country=wanted.country.upper(), area=area))
    route = Route(
        org=org, agent=wanted.agent, channel=wanted.channel, number=for_sale, env=env, managed=True
    )
    fleets = await orgs.fleets(exchange.pool)
    looked = Looked(
        route=route,
        fence=_fence(org, for_sale, None, ()),
        account=None,
        networks=(),
        fleet=orgs.fleet_of(fleets, env),
        domain=domain,
        at_twilio=AtTwilio(
            twilio=twilio,
            number=None,
            trunk=await twilio.trunk_pointing_at(origination_uri(domain)),
            for_sale=for_sale,
        ),
    )
    return await _looked_on_the_sfu(exchange, looked)


async def _at_twilio(
    exchange: Exchange, account: TwilioAccount, number: str, *, move: bool
) -> AtTwilio:
    twilio = Twilio(exchange.http, account)
    owned = await twilio.number(number)
    if owned is None:
        raise NotFound(NOT_ON_ACCOUNT.format(number=number, account=account.account_sid))
    trunk = await twilio.trunk_pointing_at(origination_uri(_domain(exchange)))
    elsewhere = owned.trunk_sid is not None and (trunk is None or owned.trunk_sid != trunk.sid)
    if elsewhere and owned.trunk_sid is not None and not move:
        where = await twilio.originations(owned.trunk_sid)
        other = await twilio.trunk(owned.trunk_sid)
        raise Conflict(
            ON_ANOTHER_TRUNK.format(
                number=number,
                trunk=other.friendly_name or other.sid,
                account=account.account_sid,
                where=", ".join(where) or "nowhere",
            )
        )
    return AtTwilio(twilio=twilio, number=owned, trunk=trunk)


async def _looked_on_the_sfu(exchange: Exchange, looked: Looked) -> Looked:
    route, fence = looked.route, looked.fence
    number = str(route.number)
    looked.routed = await routes.of_number(exchange.pool, route.org, number)
    if fence is None:
        return looked
    listing = await _trunks_listing(exchange.server, number)
    stranger = next((one for one in listing if not _is_the_orgs(one.name, route.org)), None)
    if stranger is not None:
        raise Conflict(HELD_ELSEWHERE.format(number=number, trunk=stranger.name))
    looked.leaving = [one for one in listing if one.name != fence.trunk]
    looked.trunk = await _trunk_named(exchange.server, fence.trunk)
    looked.rule = await _rule_named(exchange.server, _rule_name(route.org, route.env))
    other = "sandbox" if route.env == "production" else "production"
    looked.other_rule = await _rule_named(exchange.server, _rule_name(route.org, other))
    return looked


# The kind of an account is read here, where it decides the fence, and nowhere else on the SFU.
def _fence(org: str, number: str, held: Carrier | None, networks: tuple[str, ...]) -> Fence:
    if held is None:
        if networks:
            return Fence(trunk=f"{org}:{number}", networks=networks)
        return Fence(trunk=org, networks=TWILIO_SIGNALLING)
    match held.account:
        case TwilioAccount() | WhatsappAccount():
            return Fence(trunk=org, networks=TWILIO_SIGNALLING)
        case SipPeer():
            peer = held.account
            return Fence(
                trunk=f"{org}:{peer.username}",
                networks=tuple(peer.addresses),
                username=peer.username,
                password=peer.password,
            )


async def _hooked(exchange: Exchange, looked: Looked) -> None:
    route, number = looked.route, str(looked.route.number)
    at = looked.at_twilio
    if at is not None and at.number is not None:
        trunk = at.trunk or await at.twilio.trunk_made(
            looked.domain, origination_uri(looked.domain)
        )
        looked.at_twilio = replace(at, trunk=trunk)
        if at.number.trunk_sid not in {None, trunk.sid}:
            await at.twilio.detach(str(at.number.trunk_sid), at.number.sid)
        if at.number.trunk_sid != trunk.sid:
            await at.twilio.attach(trunk.sid, at.number.sid)
    if looked.fence is not None:
        # Off the other world's rule first: two rules of one trunk never list one number.
        if looked.other_rule is not None and number in looked.other_rule.numbers:
            kept = [one for one in looked.other_rule.numbers if one != number]
            await _rule_numbered(exchange.server, looked.other_rule, kept)
        for leaving in looked.leaving:
            kept = [one for one in leaving.numbers if one != number]
            await _numbered(exchange.server, route.org, leaving, kept)
        trunk_id = await _admitted(exchange.server, looked.fence, looked.trunk, number)
        world = World(route.org, route.env, looked.fleet)
        await _ruled(exchange.server, world, [trunk_id], number)
    await routes.put(exchange.pool, route, account=looked.account, networks=looked.networks)


def _steps(looked: Looked, *, done: bool) -> list[str]:
    route, number = looked.route, str(looked.route.number)
    made = "done" if done else "to do"
    steps: list[str] = []
    at = looked.at_twilio
    if at is not None:
        if at.for_sale is not None:
            steps.append(f"Twilio: buy {at.for_sale} on the box's account: {made}")
        pointed = f"Twilio: a trunk sending calls to {origination_uri(looked.domain)}"
        steps.append(f"{pointed}: {'stands' if at.trunk else made}")
        on_it = (
            at.number is not None and at.trunk is not None and at.number.trunk_sid == at.trunk.sid
        )
        steps.append(f"Twilio: {number} attached to it: {'stands' if on_it else made}")
    fence = looked.fence
    if fence is not None:
        steps += [f"LiveKit: {number} off trunk {one.name}: {made}" for one in looked.leaving]
        admits = looked.trunk is not None and number in looked.trunk.numbers
        steps.append(
            f"LiveKit: trunk {fence.trunk} admits {number}: {'stands' if admits else made}"
        )
        rule = _rule_name(route.org, route.env)
        ruled = looked.rule is not None and number in looked.rule.numbers
        steps.append(
            f"LiveKit: rule {rule} sends it to fleet {looked.fleet}: {'stands' if ruled else made}"
        )
    routed = looked.routed == route
    steps.append(
        f"route: {number} to {route.agent} in the {route.env}: {'stands' if routed else made}"
    )
    return steps


def _domain(exchange: Exchange) -> str:
    if not exchange.domain:
        raise NotAvailable(NO_DOMAIN)
    return exchange.domain


async def _the_boxs_twilio(exchange: Exchange) -> TwilioAccount:
    held = (await vault.box_credentials(exchange.pool, exchange.vault)).get(THE_BOXS)
    try:
        return TwilioAccount.model_validate(held)
    except ValidationError:
        raise NotAvailable(NO_BOX_TWILIO) from None


# ── the SFU ──


@dataclass(frozen=True)
class World:
    """An org's world on the SFU: its rule, and the fleet the rule dispatches."""

    org: str
    env: Env
    fleet: str


def _rule_name(org: str, env: Env) -> str:
    return f"{org}:{env}"


# An org id holds no colon, so `{org}` and `{org}:…` can never be another org's.
def _is_the_orgs(name: str, org: str) -> bool:
    return name == org or name.startswith(f"{org}:")


async def _trunks_listing(server: api.LiveKitAPI, number: str) -> list[SIPInboundTrunkInfo]:
    listed = await server.sip.list_inbound_trunk(ListSIPInboundTrunkRequest(numbers=[number]))
    return [one for one in listed.items if number in one.numbers]


async def _trunk_named(server: api.LiveKitAPI, name: str) -> SIPInboundTrunkInfo | None:
    listed = await server.sip.list_inbound_trunk(ListSIPInboundTrunkRequest())
    return next((one for one in listed.items if one.name == name), None)


async def _rule_named(server: api.LiveKitAPI, name: str) -> SIPDispatchRuleInfo | None:
    listed = await server.sip.list_dispatch_rule(ListSIPDispatchRuleRequest())
    return next((one for one in listed.items if one.name == name), None)


async def _admitted(
    server: api.LiveKitAPI, fence: Fence, standing: SIPInboundTrunkInfo | None, number: str
) -> str:
    if standing is None:
        info = SIPInboundTrunkInfo(
            name=fence.trunk,
            numbers=[number],
            allowed_addresses=list(fence.networks),
            auth_username=fence.username,
            auth_password=fence.password,
        )
        made = await server.sip.create_inbound_trunk(CreateSIPInboundTrunkRequest(trunk=info))
        return made.sip_trunk_id
    fenced = sorted(standing.allowed_addresses) == sorted(fence.networks)
    if number in standing.numbers and fenced and standing.auth_username == fence.username:
        return standing.sip_trunk_id
    standing.numbers[:] = sorted({*standing.numbers, number})
    standing.allowed_addresses[:] = list(fence.networks)
    standing.auth_username, standing.auth_password = fence.username, fence.password
    await server.sip.update_inbound_trunk(standing.sip_trunk_id, standing)
    return standing.sip_trunk_id


# A trunk or a rule that lists no number takes every number: one emptied is deleted, and so is
# a rule left with no trunk.
async def _numbered(
    server: api.LiveKitAPI, org: str, trunk: SIPInboundTrunkInfo, numbers: list[str]
) -> None:
    if numbers:
        await server.sip.update_inbound_trunk_fields(trunk.sip_trunk_id, numbers=numbers)
        return
    await server.sip.delete_trunk(DeleteSIPTrunkRequest(sip_trunk_id=trunk.sip_trunk_id))
    listed = await server.sip.list_dispatch_rule(ListSIPDispatchRuleRequest())
    for rule in listed.items:
        if not _is_the_orgs(rule.name, org) or trunk.sip_trunk_id not in rule.trunk_ids:
            continue
        kept = [one for one in rule.trunk_ids if one != trunk.sip_trunk_id]
        if not kept:
            await _rule_numbered(server, rule, [])
            continue
        rule.trunk_ids[:] = kept
        await server.sip.update_dispatch_rule(rule.sip_dispatch_rule_id, rule)


# One rule per world: its `numbers` are that world's, so a number changes world between two
# lists and never between trunks. livekit refuses two rules of one trunk listing one number.
async def _ruled(server: api.LiveKitAPI, world: World, trunks: Sequence[str], number: str) -> None:
    # A rule with no trunk dispatches every trunk's calls.
    if not trunks:
        return
    name, fleet = _rule_name(world.org, world.env), world.fleet
    standing = await _rule_named(server, name)
    said = routes.written(Dispatch(org=world.org, env=world.env))
    dispatch = RoomAgentDispatch(agent_name=fleet, metadata=said)
    if standing is None:
        info = SIPDispatchRuleInfo(
            name=name,
            trunk_ids=sorted(set(trunks)),
            numbers=[number],
            rule=SIPDispatchRule(
                dispatch_rule_individual=SIPDispatchRuleIndividual(room_prefix=ROOM_PREFIX)
            ),
            room_config=RoomConfiguration(agents=[dispatch]),
        )
        await server.sip.create_dispatch_rule(CreateSIPDispatchRuleRequest(dispatch_rule=info))
        return
    wanted_trunks = sorted({*standing.trunk_ids, *trunks})
    fleets = [one.agent_name for one in standing.room_config.agents]
    if (
        number in standing.numbers
        and list(standing.trunk_ids) == wanted_trunks
        and fleets == [fleet]
    ):
        return
    standing.trunk_ids[:] = wanted_trunks
    standing.numbers[:] = sorted({*standing.numbers, number})
    standing.room_config.CopyFrom(RoomConfiguration(agents=[dispatch]))
    await server.sip.update_dispatch_rule(standing.sip_dispatch_rule_id, standing)


async def _rule_numbered(
    server: api.LiveKitAPI, rule: SIPDispatchRuleInfo, numbers: list[str]
) -> None:
    if not numbers:
        gone = DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule.sip_dispatch_rule_id)
        await server.sip.delete_dispatch_rule(gone)
        return
    rule.numbers[:] = numbers
    await server.sip.update_dispatch_rule(rule.sip_dispatch_rule_id, rule)


# ── reconcile at start ──

REBUILT = "SIP rebuilt from the tables: %d numbers stand, %d orgs refused"
TO_REBUILD = """
SELECT org, number, env, account, networks FROM routes
WHERE channel = 'phone' AND number IS NOT NULL ORDER BY org, added_at, number
"""


@dataclass(frozen=True)
class Rebuilt:
    """What the reconcile found: numbers standing on the SFU, orgs it could not rebuild."""

    numbers: int
    refused: list[str]


# LiveKit keeps its trunks and rules in Redis, which can be emptied; the tables are the truth.
# Nothing is deleted, and the carrier's side is never touched.
async def rebuild(exchange: Exchange) -> Rebuilt:
    """Admit every routed phone number again on the SFU, with its fence and its world's rule."""
    async with exchange.pool.connection() as connection:
        rows = await (await connection.execute(TO_REBUILD)).fetchall()
    fleets = await orgs.fleets(exchange.pool)
    stood, refused = 0, list[str]()
    for org in dict.fromkeys(str(row["org"]) for row in rows):
        try:
            held = {one.id: one for one in await carriers_of(exchange.pool, exchange.vault, org)}
            for row in (one for one in rows if one["org"] == org):
                await _stands(exchange, row, held.get(row["account"] or ""), fleets)
                stood += 1
        except (api.TwirpError, httpx.HTTPError):
            logger.warning("the SFU refused org %s's numbers: the others go on", org, exc_info=True)
            refused.append(org)
    logger.info(REBUILT, stood, len(refused))
    return Rebuilt(numbers=stood, refused=refused)


async def _stands(
    exchange: Exchange, row: DictRow, held: Carrier | None, fleets: orgs.Fleets
) -> None:
    org, number, env = str(row["org"]), str(row["number"]), row["env"]
    fence = _fence(org, number, held, tuple(row["networks"] or ()))
    trunk = await _trunk_named(exchange.server, fence.trunk)
    trunk_id = await _admitted(exchange.server, fence, trunk, number)
    world = World(org, env, orgs.fleet_of(fleets, env))
    await _ruled(exchange.server, world, [trunk_id], number)


# ── outbound: provisioning an account to dial through ──

CALLING_FROM = """
SELECT number, account FROM routes
WHERE org = %(org)s AND env = %(env)s AND channel = 'phone' ORDER BY added_at, number
"""


class Guards(BaseModel):
    """The org's dial guards: what it may dial and how often."""

    model_config = ConfigDict(frozen=True)

    dial_anywhere: bool = False
    per_minute: Annotated[int, Field(ge=0)] = DIALS_A_MINUTE
    per_day: Annotated[int, Field(ge=0)] = DIALS_A_DAY
    max_duration_s: Annotated[int, Field(ge=0)] = LONGEST_CALL_S


@dataclass(frozen=True)
class Standing:
    """Whether the org can place a call through an account, and what is still missing."""

    ready: bool
    kind: str | None
    from_numbers: list[str]
    steps_missing: list[str]
    guards: Guards


@dataclass(frozen=True)
class Provisioned:
    """What provisioning did, or would do."""

    steps: list[str]
    dry_run: bool
    ready: bool
    trunk: str | None = None
    address: str | None = None


async def standing(exchange: Exchange, corner: Corner, account: str | None = None) -> Standing:
    """Whether the org can dial out in the world, one sentence per thing missing, in order."""
    guards = await guards_of(exchange.pool, corner.org)
    numbers = [str(row["number"]) for row in await _calling_from(exchange.pool, corner)]
    try:
        held = await carrier(exchange.pool, exchange.vault, corner.org, account)
    except NotFound as missing:
        return Standing(
            ready=False,
            kind=None,
            from_numbers=numbers,
            steps_missing=[str(missing)],
            guards=guards,
        )
    missing = _missing_to_dial(held)
    if not numbers:
        missing.append(f"the org answers at no phone number in the {corner.env}: a call shows one")
    return Standing(
        ready=not missing,
        kind=held.account.kind,
        from_numbers=numbers,
        steps_missing=missing,
        guards=guards,
    )


async def plan_outbound(exchange: Exchange, org: str, account: str | None = None) -> Provisioned:
    """What provisioning would write, and nothing written."""
    held = await carrier(exchange.pool, exchange.vault, org, account)
    return await _provisioned(exchange, held, dry_run=True)


async def provision_outbound(
    exchange: Exchange, org: str, account: str | None = None
) -> Provisioned:
    """Make the account dialable: Twilio's termination and a credential; a peer needs nothing."""
    held = await carrier(exchange.pool, exchange.vault, org, account)
    return await _provisioned(exchange, held, dry_run=False)


# The kind decides how a leg is dialled out, here and in `_dialled_through`.
def _missing_to_dial(held: Carrier) -> list[str]:
    match held.account:
        case TwilioAccount():
            return [] if held.outbound else [NOT_PROVISIONED.format(account=held.id)]
        case SipPeer():
            return [] if held.account.outbound_host else [NO_OUTBOUND_HOST.format(account=held.id)]
        case WhatsappAccount():
            return [f"{held.id} is a WhatsApp number: it places no call"]


# This box makes nothing on somebody else's switch: a peer is dialled where it said.
async def _provisioned(exchange: Exchange, held: Carrier, *, dry_run: bool) -> Provisioned:
    match held.account:
        case TwilioAccount():
            return await _twilio_provisioned(exchange, held, held.account, dry_run=dry_run)
        case SipPeer() if held.account.outbound_host:
            peer = held.account
            where = f"{peer.outbound_host} over {peer.outbound_transport}"
            return Provisioned(
                steps=[f"SIP peer: dialled at {where}: stands"],
                dry_run=dry_run,
                ready=True,
                address=peer.outbound_host,
            )
        case SipPeer() | WhatsappAccount():
            raise Conflict(_missing_to_dial(held)[0])


async def _twilio_provisioned(
    exchange: Exchange, held: Carrier, account: TwilioAccount, *, dry_run: bool
) -> Provisioned:
    domain = _domain(exchange)
    twilio = Twilio(exchange.http, account)
    trunk = await twilio.trunk_pointing_at(origination_uri(domain))
    host = termination_host(domain, account.account_sid)
    # One list per trunk, as the trunk is per account and box; one credential per org on it,
    # so two orgs that brought the same account each dial with their own.
    name = host.removesuffix(TERMINATION_SUFFIX)
    username = f"pinecall-{held.org}".replace("_", "-")
    listed = await twilio.credential_list(name)
    holds = listed is not None and username in await twilio.usernames_in(listed)
    if holds and held.outbound is None:
        raise Conflict(
            CREDENTIALS_LOST.format(account=account.account_sid, username=username, name=name)
        )
    on_trunk = (
        trunk is not None and listed is not None and listed in await twilio.lists_on(trunk.sid)
    )
    made = "to do" if dry_run else "done"
    pointed = f"Twilio: a trunk sending calls to {origination_uri(domain)}"
    steps = [
        f"{pointed}: {'stands' if trunk else made}",
        f"Twilio: dialled at {host}: {'stands' if trunk and trunk.domain_name == host else made}",
        f"Twilio: credential list {name}: {'stands' if listed else made}",
        f"Twilio: the org's credential {username} on it: {'stands' if holds else made}",
        f"Twilio: the trunk asks for it: {'stands' if on_trunk else made}",
    ]
    if dry_run:
        return Provisioned(steps=steps, dry_run=True, ready=False)
    trunk = trunk or await twilio.trunk_made(domain, origination_uri(domain))
    if trunk.domain_name != host:
        await twilio.terminated(trunk.sid, host)
    listed = listed or await twilio.credential_list_made(name)
    outbound = held.outbound
    if not holds:
        outbound = Termination(host=host, username=username, password=secrets.token_urlsafe(24))
        await twilio.credential_added(listed, outbound.username, outbound.password)
    if not on_trunk:
        await twilio.list_attached(trunk.sid, listed)
    await _kept(exchange.pool, exchange.vault, replace(held, outbound=outbound))
    return Provisioned(steps=steps, dry_run=False, ready=True, trunk=trunk.sid, address=host)


# ── the guards: shape, stranger, pace, one ledger row an attempt ──

POLICY = (
    "SELECT dial_anywhere, per_minute, per_day, max_duration_s FROM dial_policy WHERE org = %(org)s"
)
PUT_POLICY = """
INSERT INTO dial_policy (org, dial_anywhere, per_minute, per_day, max_duration_s)
VALUES (%(org)s, %(dial_anywhere)s, %(per_minute)s, %(per_day)s, %(max_duration_s)s)
ON CONFLICT (org) DO UPDATE SET dial_anywhere = excluded.dial_anywhere,
    per_minute = excluded.per_minute, per_day = excluded.per_day,
    max_duration_s = excluded.max_duration_s, set_at = now()
"""
# Two dials of one org cannot both take the last slot: the lock holds until the row is written.
LOCKED = "SELECT pg_advisory_xact_lock(hashtext('dials:' || %(org)s))"
PACED = """
WITH counted AS (
    SELECT count(*) FILTER (WHERE at > now() - interval '1 minute') AS minute,
           count(*) AS day
    FROM dials WHERE org = %(org)s AND at > now() - interval '1 day'
), judged AS (
    SELECT minute, day, CASE WHEN minute >= %(per_minute)s THEN 'too_fast'
                             WHEN day >= %(per_day)s THEN 'too_many' END AS refused
    FROM counted
)
INSERT INTO dials (org, env, agent, call, dialled, shown, asked_by, refused)
SELECT %(org)s, %(env)s, %(agent)s, CASE WHEN judged.refused IS NULL THEN %(call)s END,
       %(dialled)s, %(shown)s, %(asked_by)s, judged.refused
FROM judged
RETURNING refused, (SELECT minute FROM judged) AS minute, (SELECT day FROM judged) AS day
"""
REFUSED = """
INSERT INTO dials (org, env, agent, dialled, shown, asked_by, refused)
VALUES (%(org)s, %(env)s, %(agent)s, %(dialled)s, %(shown)s, %(asked_by)s, %(refused)s)
"""
# The first leg was judged when it was placed: its ledger row names the call.
PLACED = """
SELECT 1 FROM dials WHERE org = %(org)s AND call = %(call)s AND dialled = %(dialled)s
  AND refused IS NULL
"""


@dataclass(frozen=True)
class Asking:
    """One dial asked for: whose, to whom, shown as what, by whom, as which call."""

    corner: Corner
    agent: str
    to: str
    shown: str | None
    asked_by: str
    call: str


async def guards_of(pool: Pool, org: str) -> Guards:
    """The org's guards; a column nobody set is the default."""
    async with pool.connection() as connection:
        row = await (await connection.execute(POLICY, {"org": org})).fetchone()
    if row is None:
        return Guards()
    return Guards.model_validate({name: value for name, value in row.items() if value is not None})


async def put_guards(pool: Pool, org: str, guards: Guards) -> None:
    """Replace the org's guards whole."""
    async with pool.connection() as connection:
        await connection.execute(PUT_POLICY, {"org": org, **guards.model_dump()})


async def judged(pool: Pool, asking: Asking) -> Guards:
    """Every guard on a call placed cold: shape, a stranger, the pace; one ledger row whatever."""
    destination = await _shaped(pool, asking)
    guards = await guards_of(pool, asking.corner.org)
    reached = guards.dial_anywhere or await ever_reached(
        pool, asking.corner.org, asking.corner.env, destination
    )
    if not reached:
        await _refused(pool, asking, STRANGER)
        raise NotAllowed(
            NOT_ONE_OF_OURS.format(number=destination, env=asking.corner.env, guard=STRANGER)
        )
    await _paced(pool, asking, guards)
    return guards


# A transfer target need not have called: the stranger fence stays off, the pace caps a loop.
async def judged_second_leg(pool: Pool, asking: Asking) -> Guards:
    """The shape and the pace on a leg dialled into a live call; the call's own first leg passes."""
    async with pool.connection() as connection:
        placed = await (
            await connection.execute(
                PLACED, {"org": asking.corner.org, "call": asking.call, "dialled": asking.to}
            )
        ).fetchone()
    guards = await guards_of(pool, asking.corner.org)
    if placed is not None:
        return guards
    await _shaped(pool, asking)
    await _paced(pool, asking, guards)
    return guards


def destination_of(number: str) -> str:
    """The number a dial may reach, or DeclarationRefused saying why not."""
    said = parse_e164(number)
    digits = said.removeprefix("+")
    code = next((digits[:length] for length in (3, 2, 1) if digits[:length] in CALLING_CODES), None)
    if code is None:
        raise DeclarationRefused(NOT_A_COUNTRY.format(number=said))
    if code in NEVER_DIALLED:
        raise DeclarationRefused(A_BILL.format(number=said, code=code))
    national = digits[len(code) :]
    if len(national) < SHORTEST_NATIONAL:
        raise DeclarationRefused(TOO_SHORT.format(number=said, digits=len(national), code=code))
    return said


async def _shaped(pool: Pool, asking: Asking) -> str:
    try:
        return destination_of(asking.to)
    except DeclarationRefused as malformed:
        await _refused(pool, asking, SHAPE)
        raise DeclarationRefused(f"{malformed} ({SHAPE})") from None


async def _refused(pool: Pool, asking: Asking, guard: str) -> None:
    async with pool.connection() as connection:
        await connection.execute(REFUSED, {**_ledger(asking), "refused": guard})


async def _paced(pool: Pool, asking: Asking, guards: Guards) -> None:
    asked = {
        **_ledger(asking),
        "call": asking.call,
        "per_minute": guards.per_minute,
        "per_day": guards.per_day,
    }
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(LOCKED, {"org": asking.corner.org})
        row = await (await connection.execute(PACED, asked)).fetchone()
    if row is None or row["refused"] is None:
        return
    if row["refused"] == TOO_FAST:
        said = A_BURST.format(used=row["minute"], limit=guards.per_minute, guard=TOO_FAST)
    else:
        said = A_DAYS_WORTH.format(used=row["day"], limit=guards.per_day, guard=TOO_MANY)
    raise QuotaExhausted(said)


def _ledger(asking: Asking) -> dict[str, str | None]:
    return {
        "org": asking.corner.org,
        "env": asking.corner.env,
        "agent": asking.agent,
        "dialled": asking.to,
        "shown": asking.shown,
        "asked_by": asking.asked_by,
    }


# ── placing a call, and the leg's trunk ──


@dataclass(frozen=True)
class Placing:
    """A call asked for: corner, agent, the far end, the number shown, who asked, the day."""

    corner: Corner
    agent: str
    to: str
    shown: str | None
    asked_by: str
    today: date


@dataclass(frozen=True)
class Placed:
    """The call a dial became, before the far end has heard anything ring."""

    call: str
    to: str
    shown: str


async def place(exchange: Exchange, logs: Logs, placing: Placing, *, running: int) -> Placed:
    """Place the call: number shown, account, guards, minutes, the log, then the dispatch."""
    corner = placing.corner
    doors = [
        one
        for one in await routes.of_org(exchange.pool, corner.org, corner.env)
        if one.agent == placing.agent and one.channel == "phone"
    ]
    if not doors:
        raise NotFound(NO_PHONE_DOOR.format(agent=placing.agent, env=corner.env))
    shown = placing.shown or str(doors[0].number)
    route = next((one for one in doors if one.number == shown), None)
    if route is None:
        raise DeclarationRefused(
            NOT_OUR_NUMBER.format(shown=shown, agent=placing.agent, env=corner.env)
        )
    held = await _dials_through(exchange, corner, shown)
    call = new_call_id()
    asking = Asking(corner, placing.agent, placing.to, shown, placing.asked_by, call)
    guards = await judged(exchange.pool, asking)
    await admission.admit_call(exchange.pool, corner.org, corner.env, running=running)
    context = CallContext(
        call=call,
        channel="phone",
        direction="outbound",
        caller=placing.to,
        route=route,
        today=placing.today,
        holder=corner.holder or None,
    )
    await logs.store.claim(call, placing.agent, corner.org, Claim(corner))
    log = logs.writing(call, placing.agent)
    kind, said = arrival_entry(context, shown, asked_by=placing.asked_by)
    await log.append(kind, said)
    fleets = await orgs.fleets(exchange.pool)
    dispatch = Dispatch(
        agent=placing.agent,
        org=corner.org,
        env=corner.env,
        holder=corner.holder or None,
        direction="outbound",
        caller=placing.to,
        dial=Dialling(
            trunk=held.id, to=placing.to, shown=shown, max_duration_s=guards.max_duration_s
        ),
    )
    try:
        await routes.dispatched(exchange.server, call, orgs.fleet_of(fleets, corner.env), dispatch)
    except api.TwirpError as refused:
        ended = CallEnded(
            reason="dial_failed", ended_by="platform", ended_at=time.time(), duration_s=0.0
        )
        await log.append("call.ended", ended.written())
        await log.seal()
        logs.forget(call)
        raise UpstreamFailed(DID_NOT_DIAL.format(said=refused.message)) from refused
    return Placed(call=call, to=placing.to, shown=shown)


async def leg_through(exchange: Exchange, asking: Asking) -> LegTrunk:
    """How a leg of a live call is dialled, after its guards: the account of the number shown."""
    await judged_second_leg(exchange.pool, asking)
    corner = asking.corner
    shown = asking.shown
    if shown is None:
        doors = [
            one
            for one in await routes.of_org(exchange.pool, corner.org, corner.env)
            if one.agent == asking.agent and one.channel == "phone"
        ]
        if not doors:
            raise NotFound(NO_PHONE_DOOR.format(agent=asking.agent, env=corner.env))
        shown = str(doors[0].number)
    return _dialled_through(await _dials_through(exchange, corner, shown), shown)


async def _dials_through(exchange: Exchange, corner: Corner, shown: str) -> Carrier:
    rows = await _calling_from(exchange.pool, corner)
    account = next((row["account"] for row in rows if row["number"] == shown), None)
    if account is None:
        raise Conflict(DIALS_THROUGH_NOTHING.format(shown=shown))
    held = await carrier(exchange.pool, exchange.vault, corner.org, str(account))
    missing = _missing_to_dial(held)
    if missing:
        raise Conflict(missing[0])
    return held


def _dialled_through(held: Carrier, shown: str) -> LegTrunk:
    match held.account:
        case TwilioAccount():
            termination = held.outbound
            if termination is None:
                raise Conflict(NOT_PROVISIONED.format(account=held.id))
            return LegTrunk(
                hostname=termination.host,
                transport="auto",
                username=termination.username,
                password=termination.password,
                shown=shown,
            )
        case SipPeer():
            peer = held.account
            return LegTrunk(
                hostname=str(peer.outbound_host),
                transport=peer.outbound_transport,
                username=peer.outbound_username or peer.username,
                password=peer.outbound_password or peer.password,
                shown=shown,
            )
        case WhatsappAccount():
            raise Conflict(_missing_to_dial(held)[0])


def sip_config(leg: LegTrunk) -> SIPOutboundConfig:
    """The inline trunk livekit dials a leg through."""
    return SIPOutboundConfig(
        hostname=leg.hostname,
        transport=TRANSPORTS[leg.transport],
        auth_username=leg.username,
        auth_password=leg.password,
    )


async def _calling_from(pool: Pool, corner: Corner) -> list[DictRow]:
    async with pool.connection() as connection:
        return await (
            await connection.execute(CALLING_FROM, {"org": corner.org, "env": corner.env})
        ).fetchall()


# ── the sealed rows ──


async def _kept(pool: Pool, sealed: MultiFernet, held: Carrier) -> None:
    secret = _Sealed(account=held.account, outbound=held.outbound)
    row = {
        "org": held.org,
        "kind": held.account.kind,
        "account": held.id,
        "label": held.account.label,
        "ciphertext": vault.sealed(sealed, secret.model_dump(mode="json")),
    }
    async with pool.connection() as connection:
        await connection.execute(PUT_CARRIER, row)


def _opened(sealed: MultiFernet, org: str, row: DictRow) -> Carrier | None:
    secret = vault.opened(sealed, str(row["ciphertext"]))
    try:
        kept = _Sealed.model_validate(secret)
    except ValidationError:
        logger.warning("org %s's carrier account %s is not one this box reads", org, row["account"])
        return None
    return Carrier(org=org, account=kept.account, outbound=kept.outbound)
