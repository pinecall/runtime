"""Who speaks: the voice of a call, one vendor per file."""

from pinecall.providers.registry import Speech, Vendors

VENDORS: Vendors[Speech] = Vendors("tts", __name__)

# The vendor a voice runs on when the agent named none (2026-09-25: Cartesia, which ElevenLabs was
# until it stopped answering). Here and not in pipeline.py because voices.py judges a bare word by
# who will speak it, and pipeline.py imports voices.py.
DEFAULT_TTS = "cartesia"
