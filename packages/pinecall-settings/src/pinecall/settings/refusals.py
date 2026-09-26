"""The one misconfiguration the settings refuse at startup, in the sentence every process says."""

from pinecall.errors import PinecallError

# Said in one sentence, by every process and every verb, since each reads the settings first.
NOBODY_TO_ASK = "a sandbox instance asks production who a person is: set PINECALL_IDENTITY_URL"


class NobodyToAsk(PinecallError):
    """A sandbox instance started with no PINECALL_IDENTITY_URL. Nothing runs until it has one."""
