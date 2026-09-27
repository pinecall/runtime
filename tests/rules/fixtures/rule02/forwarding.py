"""Fixture: three pass-through functions and one that is not."""


def open_call(call: str, org: str) -> str:
    """Forward, arguments in another order."""
    return serve(org, call)


async def seal(call: str) -> str:
    """Forward, awaited."""
    return await serve_async(call)


class Store:
    """Forward to an attribute."""

    def __init__(self) -> None:
        self.inner = Inner()

    def read(self, key: str) -> str:
        """Forward."""
        return self.inner.read(key)


def not_forwarding(call: str) -> str:
    """Not a pass-through: something is added."""
    return serve(call, "default")


def serve(call: str, org: str) -> str:
    """The real thing."""
    return f"{org}:{call}"


async def serve_async(call: str) -> str:
    """The real thing."""
    return call


class Inner:
    """The real thing."""

    def read(self, key: str) -> str:
        """Read."""
        return key
