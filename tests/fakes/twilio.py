"""A Twilio account on a fake transport: numbers, trunks, credential lists, a shop."""

from dataclasses import dataclass, field

import httpx

from tests.fakes.idp import a_sid


@dataclass
class TwilioTrunkHeld:
    """One trunk of the fake account."""

    sid: str
    friendly_name: str
    domain_name: str | None = None
    origination: list[str] = field(default_factory=list[str])
    credential_lists: list[str] = field(default_factory=list[str])


type Row = dict[str, str | None]


@dataclass
class Twilio:
    """One Twilio account on a fake transport: numbers, trunks, credential lists, a shop."""

    account_sid: str = field(default_factory=lambda: a_sid("AC", 1))
    user: str = field(default_factory=lambda: a_sid("SK", 1))
    secret: str = "the key's secret"
    # Number -> (its SID, the trunk it is attached to).
    numbers: dict[str, tuple[str, str | None]] = field(
        default_factory=dict[str, tuple[str, str | None]]
    )
    trunks: dict[str, TwilioTrunkHeld] = field(default_factory=dict[str, TwilioTrunkHeld])
    # SID -> (its name, its credentials).
    credential_lists: dict[str, tuple[str, list[tuple[str, str]]]] = field(
        default_factory=dict[str, tuple[str, list[tuple[str, str]]]]
    )
    for_sale: list[str] = field(default_factory=list[str])
    # A listing longer than this comes in pages.
    page_size: int = 50
    requests: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def owns(self, number: str, *, trunk: str | None = None) -> str:
        """Give the account a number, attached to a trunk or to none; its SID."""
        sid = a_sid("PN", len(self.numbers) + 1)
        self.numbers[number] = (sid, trunk)
        return sid

    def trunk(self, name: str, *origination: str) -> str:
        """Give the account a trunk sending its calls to these URIs; its SID."""
        sid = a_sid("TK", len(self.trunks) + 1)
        found = TwilioTrunkHeld(sid=sid, friendly_name=name, origination=list(origination))
        self.trunks[sid] = found
        return sid

    def transport(self) -> httpx.MockTransport:
        """A transport that answers as the account does."""
        return httpx.MockTransport(self._answer)

    def written(self) -> list[tuple[str, str]]:
        """Every request that changed something, in order."""
        return [request for request in self.requests if request[0] != "GET"]

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        pair = httpx.BasicAuth(self.user, self.secret)
        if request.headers.get("authorization") != next(pair.auth_flow(request)).headers.get(
            "authorization"
        ):
            return httpx.Response(401, json={"code": 20003, "message": "Authenticate"})
        form = dict(httpx.QueryParams(request.content.decode()).items())
        if request.url.host == "trunking.twilio.com":
            return self._trunking(request, request.url.path.removeprefix("/v1"), form)
        return self._accounts(request, form)

    def _accounts(self, request: httpx.Request, form: dict[str, str]) -> httpx.Response:
        path = request.url.path.removeprefix(f"/2010-04-01/Accounts/{self.account_sid}")
        if path == ".json":
            return httpx.Response(200, json={"sid": self.account_sid, "friendly_name": "Clinica"})
        if path == "/IncomingPhoneNumbers.json":
            return self._numbers(request, form)
        if path.startswith("/AvailablePhoneNumbers/"):
            shown = [{"phone_number": item} for item in self.for_sale[:1]]
            return httpx.Response(200, json={"available_phone_numbers": shown})
        if path.startswith("/SIP/CredentialLists"):
            return self._lists(request, path, form)
        return httpx.Response(404, json={"message": f"no {path}"})

    def _numbers(self, request: httpx.Request, form: dict[str, str]) -> httpx.Response:
        if request.method == "POST":
            bought = form["PhoneNumber"]
            self.for_sale.remove(bought)
            return httpx.Response(201, json=_number_row(bought, self.owns(bought), None))
        found = request.url.params.get("PhoneNumber")
        rows = [_number_row(number, sid, trunk) for number, (sid, trunk) in self.numbers.items()]
        kept = [row for row in rows if found is None or row["phone_number"] == found]
        return self._paged(request, "incoming_phone_numbers", kept, by_uri=True)

    def _lists(self, request: httpx.Request, path: str, form: dict[str, str]) -> httpx.Response:
        if path != "/SIP/CredentialLists.json":
            credentials = self.credential_lists[path.split("/")[3]][1]
            if request.method == "POST":
                credentials.append((form["Username"], form["Password"]))
                return httpx.Response(201, json={"sid": a_sid("CR", len(credentials))})
            found: list[Row] = [{"username": username} for username, _ in credentials]
            return self._paged(request, "credentials", found, by_uri=True)
        if request.method == "POST":
            sid = a_sid("CL", len(self.credential_lists) + 1)
            self.credential_lists[sid] = (form["FriendlyName"], [])
            return httpx.Response(201, json={"sid": sid, "friendly_name": form["FriendlyName"]})
        rows: list[Row] = [
            {"sid": sid, "friendly_name": name} for sid, (name, _) in self.credential_lists.items()
        ]
        return self._paged(request, "credential_lists", rows, by_uri=True)

    def _trunking(self, request: httpx.Request, path: str, form: dict[str, str]) -> httpx.Response:
        parts = path.strip("/").split("/")
        if parts == ["Trunks"]:
            if request.method == "POST":
                sid = self.trunk(form["FriendlyName"])
                return httpx.Response(201, json=_trunk_row(self.trunks[sid]))
            rows = [_trunk_row(value) for value in self.trunks.values()]
            return self._paged(request, "trunks", rows, by_uri=False)
        # Twilio answers an unknown trunk with its own sentence, which reaches the person whole.
        if parts[1] not in self.trunks:
            return httpx.Response(404, json={"code": 20404, "message": f"no trunk {parts[1]}"})
        found = self.trunks[parts[1]]
        if len(parts) == 2:
            if request.method == "POST":
                if not form["DomainName"].endswith(".pstn.twilio.com"):
                    return httpx.Response(400, json={"code": 21245, "message": "Invalid domain"})
                found.domain_name = form["DomainName"]
            return httpx.Response(200, json=_trunk_row(found))
        return self._of_a_trunk(request, found, parts[2:], form)

    def _of_a_trunk(
        self,
        request: httpx.Request,
        twilio_trunk_held: TwilioTrunkHeld,
        parts: list[str],
        form: dict[str, str],
    ) -> httpx.Response:
        if parts == ["OriginationUrls"]:
            if request.method == "POST":
                twilio_trunk_held.origination.append(form["SipUrl"])
            rows = [
                {"sid": a_sid("OU", n), "sip_url": url}
                for n, url in enumerate(twilio_trunk_held.origination)
            ]
            return httpx.Response(200, json={"origination_urls": rows, "meta": {}})
        if parts[0] == "PhoneNumbers":
            wanted = {form.get("PhoneNumberSid"), parts[-1]}
            number = next(number for number, (sid, _) in self.numbers.items() if sid in wanted)
            sid, _ = self.numbers[number]
            detached = request.method == "DELETE"
            self.numbers[number] = (sid, None if detached else twilio_trunk_held.sid)
            return httpx.Response(204 if detached else 201)
        if request.method == "POST":
            twilio_trunk_held.credential_lists.append(form["CredentialListSid"])
        rows = [{"sid": sid} for sid in twilio_trunk_held.credential_lists]
        return httpx.Response(200, json={"credential_lists": rows, "meta": {}})

    def _paged(
        self, request: httpx.Request, key: str, rows: list[Row], *, by_uri: bool
    ) -> httpx.Response:
        page = int(request.url.params.get("Page", "0"))
        shown = rows[page * self.page_size : (page + 1) * self.page_size]
        more = (page + 1) * self.page_size < len(rows)
        following = request.url.copy_merge_params({"Page": str(page + 1)}) if more else None
        if by_uri:
            uri = None if following is None else f"{following.path}?{following.query.decode()}"
            return httpx.Response(200, json={key: shown, "next_page_uri": uri})
        url = None if following is None else str(following)
        return httpx.Response(200, json={key: shown, "meta": {"next_page_url": url}})


def _number_row(number: str, sid: str, trunk: str | None) -> Row:
    return {"sid": sid, "phone_number": number, "friendly_name": number, "trunk_sid": trunk}


def _trunk_row(twilio_trunk_held: TwilioTrunkHeld) -> Row:
    return {
        "sid": twilio_trunk_held.sid,
        "friendly_name": twilio_trunk_held.friendly_name,
        "domain_name": twilio_trunk_held.domain_name,
    }
