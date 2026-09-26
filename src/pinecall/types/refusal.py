"""The one error a contract raises: a declaration that breaks its rule, the rule in the message."""

from pinecall._exceptions import PinecallError


# Also a ValueError, because that is what a constructor refusing its arguments is in Python.
class DeclarationRefused(PinecallError, ValueError):
    """A contract refused what it was given. The message is the rule, as a sentence."""
