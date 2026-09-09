"""One fake vendor, in one file, registered in one line, imported by nothing but the registry."""

from pinecall.providers.registry import Asked
from tests.providers.vendors import VENDORS

DEFAULT_MODEL = "acme-the-only-one"


@VENDORS.registers("acme")
def build(asked: Asked) -> str:
    """What this vendor makes is a string, so the test reads back exactly what it was asked."""
    return asked.model or DEFAULT_MODEL
