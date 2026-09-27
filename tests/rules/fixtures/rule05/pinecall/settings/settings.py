"""Fixture: settings importing the gateway."""

from pinecall.gateway.app import build
from pinecall.gateway.api.calls import open_call

__all__ = ["build", "open_call"]
