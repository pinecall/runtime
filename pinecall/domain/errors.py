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
    """Nothing by that name in the caller's scope."""

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


class TooManyRequests(PinecallError):
    """The same name knocked too often in the last minute: a password, a code, a sign-up."""

    status = 429


class Throttled(TooManyRequests):
    """An org's family of doors took its minute's requests; the answer says when to come back."""

    def __init__(self, sentence: str, *, retry_after_s: int) -> None:
        """The refusal, and the whole seconds until the minute turns."""
        super().__init__(sentence)
        self.retry_after_s = retry_after_s


class UpstreamFailed(PinecallError):
    """A vendor, a carrier or an identity provider did not answer as it should."""

    status = 502


class GatewayRefused(UpstreamFailed):
    """The worker's gateway did not answer, or answered with a refusal; `answered` is its status."""

    def __init__(self, sentence: str, *, answered: int | None = None) -> None:
        """The refusal, and the status the gateway answered with, None when it was unreachable."""
        super().__init__(sentence)
        self.answered = answered


class AppRefused(PinecallError):
    """A tenant's app refused what a console asked of it; its own status is the answer's."""

    def __init__(self, answered: int, sentence: str) -> None:
        """The app's status and its sentence, passed on as they came."""
        super().__init__(sentence)
        self.status = answered


class NotAvailable(PinecallError):
    """This box lacks what the request needs: a key, the vault, an embedder, a domain."""

    status = 503


class EmbedderUnreachable(PinecallError):
    """The box's embedder did not answer, or answered something that is not its vectors."""

    status = 503

    def __init__(self, vendor: str, url: str, words: str) -> None:
        """The embedder, where it was asked, and what it said or why it said nothing."""
        super().__init__(f"{vendor} at {url} did not answer: {words}")
        self.words = words


class WrongWidth(PinecallError):
    """The embedder answered vectors of a width the vector columns do not hold."""

    status = 503


# Two models' vectors can share a width and still not be comparable.
class WrongModel(PinecallError):
    """A base's vectors were written by another model than the one this box embeds with."""

    status = 503

    def __init__(self, base: str, pushed: str, embeds: str) -> None:
        """The base, the model that wrote it, and the model the box embeds with now."""
        super().__init__(
            f"base {base} was pushed with {pushed}; this box embeds with {embeds}: "
            "push it again with `pinecall docs push`"
        )


class SettingsRefused(PinecallError):
    """The process cannot start with the environment it was given."""


class StoreUnreachable(PinecallError):
    """The database cannot be reached or is not usable."""

    status = 503


class MigrationsRefused(PinecallError):
    """The database and the shipped migrations disagree."""
