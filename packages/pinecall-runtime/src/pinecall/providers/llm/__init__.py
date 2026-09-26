"""Who answers: the models an agent can think with, one vendor per file."""

from pinecall.providers.registry import Chat, Vendors

VENDORS: Vendors[Chat] = Vendors("llm", __name__)
