"""A modality that exists only in this suite: the proof that a vendor needs no edit but its file."""

from pinecall.providers.registry import Vendors

VENDORS: Vendors[str] = Vendors("fake", __name__)
