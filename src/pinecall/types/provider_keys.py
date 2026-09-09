"""Whose key a call runs on: the vendors an org may bring its own key for, and what a set is."""

from collections.abc import Mapping

# The vendors an org may bring a key for: the model vendors, one file each under providers/, and
# the WhatsApp Cloud API token a message goes back out with. A door refuses any other word with
# this list in the sentence, so an operator who typed `11labs` is told what to type instead of
# storing a key nobody reads.
VENDORS: tuple[str, ...] = (
    "anthropic",
    "deepgram",
    "elevenlabs",
    "openai",
    "soniox",
    "whatsapp",
)

# One org's own keys, vendor by vendor. Empty is the common case: the box's env keys are used.
type ProviderKeys = Mapping[str, str]

# The org that brought none, spelled once: every door that reads keys falls back to this.
NO_ORG_KEYS: ProviderKeys = {}
