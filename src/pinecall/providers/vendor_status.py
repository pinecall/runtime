"""How a vendor stands on THIS box, in one word, computed once for every screen that draws it."""

# It was computed twice — in the door and in the CLI — and the two disagreed about RTZR, which has
# no single-string credential and so read as `ready` on a box that could not build it. A vendor's
# standing is one question with one answer, so it is one function, and every reader is handed the
# word rather than the three booleans it would have to combine itself.

from __future__ import annotations

from typing import Literal

from pinecall._settings import Settings
from pinecall.providers import catalog
from pinecall.providers.catalog import Provider
from pinecall.providers.livekit_inference import VENDOR as INFERENCE
from pinecall.providers.livekit_inference import has_livekit_pair
from pinecall.providers.plugin import is_installed

type Standing = Literal["ready", "no plugin", "no key", "its own"]

READY: Standing = "ready"
NO_PLUGIN: Standing = "no plugin"
NO_KEY: Standing = "no key"
# AWS's credential chain, Google's service account, RTZR's client id and secret: nothing this box
# can be handed one string for, so it is neither ready nor missing a key — it is the vendor's own
# arrangement, and the plugin either finds it in the environment or says so itself.
ITS_OWN: Standing = "its own"


def vendor_status(provider: Provider, settings: Settings) -> Standing:
    """One word for what this vendor is waiting for. `ready` is the only one that runs a call."""
    if not is_installed(provider):
        return NO_PLUGIN
    if provider.name == INFERENCE:
        # Inference bills the box's own LiveKit project, so the project's pair IS its key.
        return READY if has_livekit_pair(settings) else NO_KEY
    field = catalog.settings_field_of(provider.name)
    if field is None:
        return ITS_OWN
    return READY if getattr(settings, field, None) else NO_KEY
