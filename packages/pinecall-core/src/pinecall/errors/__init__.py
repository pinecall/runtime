"""The root of every error the runtime raises on purpose, so a caller can catch one name."""

__all__ = ["PinecallError"]


class PinecallError(Exception):
    """Catch this to catch anything Pinecall raises deliberately."""
