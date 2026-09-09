"""Who speaks: the voice of a call, one vendor per file."""

from pinecall.providers.registry import Speech, Vendors

VENDORS: Vendors[Speech] = Vendors("tts", __name__)
