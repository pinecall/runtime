"""A member's password: argon2id at rest, verified in constant time, and never anything else."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from pinecall.types import DeclarationRefused

# A password is a secret a person invented, and a person invents guessable ones: this is the one
# place the runtime hashes such a thing, and it is argon2id with the library's own defaults —
# which are the OWASP ones — so a dump of the members table is not a dump of passwords. A key is
# NOT hashed here: it is 256 bits of CSPRNG and stays on sha256 (auth/keys.py), where a slow hash
# would only slow every handshake.
_HASHER = PasswordHasher()

# Length is the one rule, because it is the one that matters against a guess: twelve characters of
# anything. Nothing about digits or symbols, which people meet with `Password1!`.
SHORTEST_PASSWORD = 12


def hashed(password: str) -> str:
    """The password as the table keeps it: an argon2id string carrying its own salt and cost."""
    if len(password) < SHORTEST_PASSWORD:
        raise DeclarationRefused(f"a password is at least {SHORTEST_PASSWORD} characters")
    return _HASHER.hash(password)


def matches(password: str, kept: str) -> bool:
    """Whether the password is the one hashed. Wrong, or a hash that is not one: both are False."""
    try:
        return _HASHER.verify(kept, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
