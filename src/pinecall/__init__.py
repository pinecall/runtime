"""Pinecall: the voice-AI runtime for contact centers. This is the public surface."""

from pinecall._version import __version__
from pinecall.errors import PinecallError

__all__ = [
    "PinecallError",
    "__version__",
]
