"""What this gateway lacks to reach a carrier: the media plane, a name, a carrier of its own."""

from pinecall.errors import PinecallError

NO_DOMAIN = "this gateway has no PINECALL_DOMAIN: a carrier cannot be pointed at a box with no name"
NO_BOX_CARRIER = (
    "this gateway has no TWILIO_ACCOUNT_SID and TWILIO_API_SECRET: it buys numbers for nobody. "
    "Bring the org's own carrier with PUT /v1/carrier and import one instead"
)


class NoMediaPlane(PinecallError):
    """This gateway has no LiveKit key pair: it cannot admit a number or put a trunk on the SFU."""


class NoDomain(PinecallError):
    """This gateway has no public name for a carrier to send its calls to."""


class NoBoxCarrier(PinecallError):
    """This gateway was given no Twilio account of its own: it has no numbers to sell."""
