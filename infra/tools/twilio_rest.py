"""The two REST calls a trunk needs — a page read and a form posted — over Twilio's basic auth."""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

TRUNKING_API = "https://trunking.twilio.com/v1"
ACCOUNTS_API = "https://api.twilio.com/2010-04-01"

# A trunk is created once and read a handful of times; nothing here is on a call path.
TIMEOUT_S = 30


class TwilioRefused(Exception):
    """Twilio answered anything but 2xx. The message carries what it said, for the terminal."""


@dataclass(frozen=True)
class Twilio:
    """One account's credentials, and the only two verbs the trunk script ever needs."""

    account_sid: str
    user: str
    password: str

    # An API key/secret pair can be revoked without touching the account token, so it is preferred
    # when both are set: a leaked SK… costs a rotation, a leaked account token costs the account.
    @classmethod
    def from_environment(cls) -> "Twilio":
        """TWILIO_ACCOUNT_SID, plus either an API key pair or the account's auth token."""
        environment = os.environ  # noqa: TID251 — a standalone script has no pinecall Settings
        account_sid = environment["TWILIO_ACCOUNT_SID"]
        key, secret = environment.get("TWILIO_API_KEY"), environment.get("TWILIO_API_SECRET")
        if key and secret:
            return cls(account_sid, key, secret)
        return cls(account_sid, account_sid, environment["TWILIO_AUTH_TOKEN"])

    def get(self, url: str) -> dict[str, Any]:
        """One resource, or one page of them, as JSON."""
        return self._send(urllib.request.Request(url))

    def post(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        """Every Twilio write takes a form body; the answer is the resource it made."""
        request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode())
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        return self._send(request)

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        pair = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        request.add_header("Authorization", f"Basic {pair}")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as answer:
                read: Any = json.load(answer)
                return dict(read)
        except urllib.error.HTTPError as refused:
            raise TwilioRefused(f"twilio {refused.code}: {refused.read().decode()[:300]}") from None
