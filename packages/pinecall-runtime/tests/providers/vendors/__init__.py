"""A modality that exists only in this suite: the proof that a vendor needs no edit but its file."""

from typing import cast

from pinecall.providers.catalog import Modality
from pinecall.providers.registry import Vendors

# `fake` is not one of the three on purpose, and the cast says so out loud: providers/catalog.py
# catalogues nothing for it, so this table holds exactly the files under this package and the
# catalogued fallback can never answer for one. That is what makes it a test of the registration
# mechanism alone — VENDORS.names here is the rows and nothing else.
VENDORS: Vendors[str] = Vendors(cast("Modality", "fake"), __name__)
