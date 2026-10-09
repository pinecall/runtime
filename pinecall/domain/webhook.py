"""Where an org's alerts go: a URL of its own, every post signed when it set a secret."""

from dataclasses import dataclass

from pinecall.domain.errors import DeclarationRefused

NOT_A_URL = "a webhook is an http(s) URL an alert can be posted to, not {said!r}"


@dataclass(frozen=True)
class Webhook:
    """The URL the org's alerts are posted to, and the secret each post is signed with."""

    url: str
    secret: str | None = None

    def __post_init__(self) -> None:
        if not self.url.startswith(("http://", "https://")):
            raise DeclarationRefused(NOT_A_URL.format(said=self.url))
