"""Whose agent a key may open: the one question the web asks, now that there is no web door."""

from __future__ import annotations

from pinecall.api.agents.holding import Registration, SocketId
from pinecall.api.agents.registry import Registry
from pinecall.auth.keys import KeyRecord, held_by


# A number is a row somebody bought and the widget is not: there is no web door to hold, and every
# agent a key opens can be talked to in a browser. So the token door asks what the chat socket
# asks — is anybody holding this agent in my world, and is it my org's — and api/calls/chat.py
# asks the same two questions inline, because it tells the two refusals apart in the sentence it
# writes back down the socket.
def reached_by(
    registry: Registry, key: KeyRecord, slug: str, app: SocketId | None = None
) -> Registration | None:
    """The registration this key reaches for this agent: its own org's, in its own world."""
    held = registry.serving(key.env, slug, app, held_by(key))
    return held if held is not None and held.org == key.org else None
