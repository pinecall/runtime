"""An org's webhook on a fake transport: every post heard, answered with the statuses told."""

from dataclasses import dataclass, field

import httpx

HOST = "hooks.example.test"


@dataclass
class Receiver:
    """The URL an org's alerts are posted to: what it heard, and how it answers each post."""

    heard: list[httpx.Request] = field(default_factory=list[httpx.Request])
    # The statuses answered in order; the last one again once they run out.
    answers: list[int] = field(default_factory=lambda: [200])

    @property
    def url(self) -> str:
        """Where the org posts: the one path the fake answers at."""
        return f"https://{HOST}/alerts"

    def answer(self, request: httpx.Request) -> httpx.Response:
        """Keep the post and answer the next status told."""
        self.heard.append(request)
        status = self.answers[min(len(self.heard), len(self.answers)) - 1]
        return httpx.Response(status, json={"ok": status < 300})
