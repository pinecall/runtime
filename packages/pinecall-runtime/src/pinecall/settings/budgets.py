"""What a turn may wait for its lookups, and a hang-up for its memory, before going on."""

from dataclasses import dataclass


# A lookup never delays a reply past its budget, and a slow model at hang-up never holds the
# seal: the numbers a session waits on memory and retrieval for, then goes on without them.
# Declared here, once, because the three fields below take their defaults from it. The two lookup
# budgets are named for the CHANNEL because each measures a different silence, and the field that
# reads each one says which (session/lookup_tools.py starts a spoken call's while the caller talks).
@dataclass(frozen=True)
class Budgets:
    """What each turn may wait for its lookups, and a hang-up for its memory, before going on."""

    voice_lookup_ms: int = 250
    text_lookup_ms: int = 3000
    remember_s: float = 8.0
    # What the seal of a spoken call waits, in all, for its queued entries to reach the platform
    # and its verdict to come back: well inside the job's own SEALING_S (worker/main.py), so a
    # gateway that is away at hang-up costs the verdict and never the seal (2026-09-26).
    seal_s: float = 20.0
