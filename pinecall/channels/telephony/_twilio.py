"""Twilio's REST API, written by hand: numbers, trunks, credential lists, the shop."""

import logging

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    TypeAdapter,
    ValidationError,
)

from pinecall.domain.errors import (
    DeclarationRefused,
    UpstreamFailed,
)
from pinecall.domain.names import Json
from pinecall.tenancy.carriers import TwilioAccount

logger = logging.getLogger(__name__)


TRUNKING = "https://trunking.twilio.com/v1"


ACCOUNTS = "https://api.twilio.com/2010-04-01"


# Never on a call's path: an import or a purchase waits for Twilio, a call never does.
TIMEOUT_S = 30.0


# livekit-sip listens here, and the fence opens it to the carrier alone.
SIP_PORT = 5060


# Termination hosts are global across Twilio; the label is `[a-z0-9-]`. Twilio wants the whole
# host in DomainName: a bare label is its error 21245.
TWILIO_SAID = "Twilio: {message}"


TERMINATION_SUFFIX = ".pstn.twilio.com"


ROOM_PREFIX = "call-"


_OBJECT: TypeAdapter[dict[str, Json]] = TypeAdapter(dict[str, Json])


TWILIO_REFUSED = "Twilio refused these credentials for {account}"


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


class Twilio:
    """One Twilio account's REST API, over the box's HTTP client."""

    def __init__(self, http: httpx.AsyncClient, account_sid: str, user: str, secret: str) -> None:
        """The account and the pair it is asked with; nothing is asked yet."""
        self.http = http
        self.sid = account_sid
        self.auth = httpx.BasicAuth(user, secret)
        self.home = f"{ACCOUNTS}/Accounts/{account_sid}"

    async def verified(self) -> bool:
        """Whether the pair opens the account."""
        answer = await self.http.get(f"{self.home}.json", auth=self.auth, timeout=TIMEOUT_S)
        if answer.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            return False
        _answer_of(answer)
        return True

    async def numbers(self) -> list[TwilioNumber]:
        """Every number the account owns, every page."""
        listed = await self._every(
            f"{self.home}/IncomingPhoneNumbers.json?PageSize=200", "incoming_phone_numbers"
        )
        return _NUMBERS.validate_python(listed)

    async def number(self, number: str) -> TwilioNumber | None:
        """The account's number, or None when it owns no such number."""
        answer = await self._get(f"{self.home}/IncomingPhoneNumbers.json", {"PhoneNumber": number})
        found = _NUMBERS.validate_python(answer.get("incoming_phone_numbers", []))
        return found[0] if found else None

    async def trunk_pointing_at(self, uri: str) -> TwilioTrunk | None:
        """The account's trunk whose origination is this URI, whatever it is named."""
        trunks = _TRUNKS.validate_python(
            await self._every(f"{TRUNKING}/Trunks?PageSize=50", "trunks")
        )
        for trunk in trunks:
            if uri in {origination.sip_url for origination in await self._originations(trunk.sid)}:
                return trunk
        return None

    async def trunk(self, sid: str) -> TwilioTrunk:
        """One trunk of the account."""
        return TwilioTrunk.model_validate(await self._get(f"{TRUNKING}/Trunks/{sid}"))

    async def originations(self, sid: str) -> list[str]:
        """Where the trunk sends a call."""
        return [origination.sip_url for origination in await self._originations(sid)]

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
            _answer_of(answer)

    async def for_sale(self, country: str, area_code: str | None) -> str | None:
        """One local voice number on sale in the country, or None."""
        params = {"VoiceEnabled": "true", "PageSize": "1"} | (
            {"AreaCode": area_code} if area_code else {}
        )
        answer = await self._get(
            f"{self.home}/AvailablePhoneNumbers/{country.upper()}/Local.json", params
        )
        found = _FOR_SALE.validate_python(answer.get("available_phone_numbers", []))
        return found[0].phone_number if found else None

    async def bought(self, number: str) -> TwilioNumber:
        """Buy the number onto the account: this costs money."""
        answer = await self._post(f"{self.home}/IncomingPhoneNumbers.json", {"PhoneNumber": number})
        return TwilioNumber.model_validate(answer)

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
        return next((found.sid for found in listed if found.friendly_name == name), None)

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
        return [credential.username for credential in _CREDENTIALS.validate_python(listed)]

    async def credential_added(self, credential_list: str, username: str, password: str) -> None:
        """One more credential on the list; Twilio shows its password this once."""
        form = {"Username": username, "Password": password}
        await self._post(
            f"{self.home}/SIP/CredentialLists/{credential_list}/Credentials.json", form
        )

    async def lists_on(self, trunk: str) -> list[str]:
        """The credential lists the trunk asks the box for."""
        listed = await self._every(f"{TRUNKING}/Trunks/{trunk}/CredentialLists", "credential_lists")
        return [found.sid for found in _LISTS.validate_python(listed)]

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
            answer = await self._get(following)
            listed = answer.get(key)
            found += listed if isinstance(listed, list) else []
            page = _Page.model_validate(answer)
            path = page.next_page_uri
            following = (
                f"https://{httpx.URL(url).host}{path}"
                if path
                else (page.meta.next_page_url if page.meta else None)
            )
        return found

    async def _get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Json]:
        answer = await self.http.get(url, params=params, auth=self.auth, timeout=TIMEOUT_S)
        return _answer_of(answer)

    async def _post(self, url: str, form: dict[str, str]) -> dict[str, Json]:
        answer = await self.http.post(url, data=form, auth=self.auth, timeout=TIMEOUT_S)
        return _answer_of(answer)


_NUMBERS: TypeAdapter[list[TwilioNumber]] = TypeAdapter(list[TwilioNumber])


_TRUNKS: TypeAdapter[list[TwilioTrunk]] = TypeAdapter(list[TwilioTrunk])


_ORIGINATIONS: TypeAdapter[list[_Origination]] = TypeAdapter(list[_Origination])


_LISTS: TypeAdapter[list[_Listed]] = TypeAdapter(list[_Listed])


_CREDENTIALS: TypeAdapter[list[_Credential]] = TypeAdapter(list[_Credential])


_FOR_SALE: TypeAdapter[list[_ForSale]] = TypeAdapter(list[_ForSale])


def origination_uri(domain: str) -> str:
    """The SIP URI a Twilio trunk sends this box's calls to."""
    return f"sip:{domain}:{SIP_PORT};transport=udp"


def termination_host(domain: str, account: str) -> str:
    """The host the box dials an account through: one per account per box, `[a-z0-9-]`."""
    named = f"{domain}-{account[-8:]}".lower()
    label = "".join(letter if letter.isalnum() else "-" for letter in named).strip("-")
    return f"{label}{TERMINATION_SUFFIX}"


def twilio_of(http: httpx.AsyncClient, account: TwilioAccount) -> Twilio:
    """The account's REST API, asked with its pair."""
    return Twilio(http, account.account_sid, account.user, account.secret)


async def verify(http: httpx.AsyncClient, account: TwilioAccount) -> None:
    """Refuse a pair Twilio does not open the account with, in our words."""
    if not await twilio_of(http, account).verified():
        raise DeclarationRefused(TWILIO_REFUSED.format(account=account.account_sid))


# Twilio's own sentence reaches the person; the pair never does.
def _answer_of(answer: httpx.Response) -> dict[str, Json]:
    if answer.is_error:
        try:
            message = _Refusal.model_validate_json(answer.content).message
        except ValidationError:
            message = f"{answer.status_code}"
        raise UpstreamFailed(TWILIO_SAID.format(message=message or answer.status_code))
    return _OBJECT.validate_json(answer.content) if answer.content else {}
