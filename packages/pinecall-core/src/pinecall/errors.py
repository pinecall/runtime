"""The root of every error the runtime raises on purpose, so a caller can catch one name."""


class PinecallError(Exception):
    """Catch this to catch anything Pinecall raises deliberately."""
