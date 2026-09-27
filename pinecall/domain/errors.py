"""The one error hierarchy: every error the runtime raises on purpose, with its HTTP status."""


class PinecallError(Exception):
    """Base of every deliberate error; the message is the sentence a person reads."""

    status = 500


class DeclarationRefused(PinecallError):
    """A value broke a contract; the message states the rule."""

    status = 400


class NotSignedIn(PinecallError):
    """No key, a wrong one, or wrong credentials."""

    status = 401


class NotAllowed(PinecallError):
    """A key that opens something, but not this."""

    status = 403


class NotFound(PinecallError):
    """Nothing by that name in the caller's corner."""

    status = 404


class Conflict(PinecallError):
    """A valid request against stored state that contradicts it."""

    status = 409


class QuotaExhausted(PinecallError):
    """A limit of the org is reached: which one, what was used and the limit, when it is one."""

    status = 429

    def __init__(
        self, sentence: str, *, quota: str | None = None, used: float = 0, limit: int = 0
    ) -> None:
        """The refusal, and the numbers the door writes into the agent's log."""
        super().__init__(sentence)
        self.quota = quota
        self.used = used
        self.limit = limit


class UpstreamFailed(PinecallError):
    """A vendor, a carrier or an identity provider did not answer as it should."""

    status = 502


class NotAvailable(PinecallError):
    """This box lacks what the request needs: a key, the vault, an embedder, a domain."""

    status = 503


class SettingsRefused(PinecallError):
    """The process cannot start with the environment it was given."""


class StoreUnreachable(PinecallError):
    """The database cannot be reached or is not usable."""

    status = 503


class MigrationsRefused(PinecallError):
    """The database and the shipped migrations disagree."""
