"""The WhatsApp Graph API as a test reads it: what went out, to whom, and on whose token."""

from __future__ import annotations

from pinecall.whatsapp.cloud_api import GraphRefused


class FakeGraph:
    """Meta, scripted: every send is remembered, and a refusal is whatever the test set."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        # The token is kept ONLY so a test can prove whose key the call ran on. Nothing in the
        # runtime ever reads a token back out of anywhere.
        self.tokens: list[str] = []
        # What Meta answers with, when a test wants it to answer with a refusal.
        self.refusing: GraphRefused | None = None

    async def send_text(self, token: str, phone_number_id: str, to: str, text: str) -> None:
        """One message out, remembered. Raises whatever refusal this fake was built with."""
        self.tokens.append(token)
        self.sent.append((phone_number_id, to, text))
        if self.refusing is not None:
            raise self.refusing
