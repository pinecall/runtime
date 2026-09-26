"""A member's password: argon2id at rest, verified in constant time, and never anything else."""

import asyncio
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from pinecall.types import DeclarationRefused

# A password is a secret a person invented, and a person invents guessable ones: this is the one
# place the runtime hashes such a thing, and it is argon2id with the library's own defaults —
# which are the OWASP ones — so a dump of the members table is not a dump of passwords. A key is
# NOT hashed here: it is 256 bits of CSPRNG and stays on sha256 (auth/keys.py), where a slow hash
# would only slow every handshake.
_HASHER = PasswordHasher()

# How short a password may be is the OPERATOR's to decide and not this runtime's: it is their box,
# their people, and their judgement about who is on it. So there is no number here — the door hands
# one in, off `PINECALL_MIN_PASSWORD` (settings/schema.py, where the default lives with it), and
# zero is a real answer meaning no rule at all. Length rather than digits or symbols, which people
# meet with `Password1!`; and what actually stops a guess is not the floor but the two things
# around it: argon2id at rest, so a dumped table is not a dumped password, and five tries a minute
# per name (auth/throttle.py), so an online guess gets nowhere.
TOO_SHORT = "a password is at least {shortest} characters"


# Both verbs leave the event loop: argon2id is SLOW ON PURPOSE — tens of milliseconds and
# 64 MiB per call, the library's defaults — and a gateway that ran it inline stalled every open
# socket and stream for the length of each login (found 2026-09-26). A thread costs nothing the
# hash does not already cost, and the loop keeps answering meanwhile.
async def hash_password(password: str, at_least: int) -> str:
    """The password as the table keeps it: an argon2id string carrying its own salt and cost."""
    if len(password) < at_least:
        raise DeclarationRefused(TOO_SHORT.format(shortest=at_least))
    return await asyncio.to_thread(_HASHER.hash, password)


# The hash a door checks a password against when NOBODY answers to the address: argon2id over a
# secret this process made up at startup and never wrote down. It never matches, and it costs
# exactly what a real one costs — a door that answered a stranger's email in a microsecond and a
# member's in a hundred milliseconds told the stranger which addresses are members, one at a time.
_NOBODYS = _HASHER.hash(secrets.token_urlsafe(32))


async def matches(password: str, kept: str | None) -> bool:
    """Whether the password is the one hashed. Wrong, a hash that is not one, or nobody's (None):
    all three are False, and all three take as long as a right answer."""
    return await asyncio.to_thread(_verified, password, kept)


def _verified(password: str, kept: str | None) -> bool:
    """The verification itself, on whichever thread runs it."""
    try:
        return _HASHER.verify(_NOBODYS if kept is None else kept, password) and kept is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
