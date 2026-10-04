"""A developer's own phone at a production number, carried to the sandbox's LiveKit by SIP."""

import hashlib
import hmac
from collections.abc import Mapping

from livekit import api
from livekit.protocol.webhook import WebhookEvent

from pinecall.channels import rooms
from pinecall.channels.rooms import Dispatch

# The sandbox's trunk and its rule that admit production's hand-overs are both named so.
HAND_OVER = "hand-over"

USERNAME = "pinecall-hand-over"

# What the trunk's password is drawn under, from the LiveKit secret both worlds' clusters share.
PAIR_OF = b"pinecall: a ring handed from production to the sandbox"

# The leg production's room dials, by its identity there.
LEG_PREFIX = "hand_over_"

LEFT = "participant_left"

# Whose sandbox copy the ring is for rides the leg's INVITE, a header each; the sandbox's trunk
# turns each into an attribute of its leg.
ORG = "pinecall.org"
AGENT = "pinecall.agent"
HOLDER = "pinecall.holder"

HEADER_OF: Mapping[str, str] = {
    ORG: "X-Pinecall-Org",
    AGENT: "X-Pinecall-Agent",
    HOLDER: "X-Pinecall-Holder",
}

# The sandbox's trunk's map of the leg's headers to its attributes.
ATTRIBUTE_OF: Mapping[str, str] = {header: attribute for attribute, header in HEADER_OF.items()}


def password_of(secret: str) -> str:
    """The hand-over trunk's password: whoever holds LiveKit's secret could rewrite the trunk."""
    return hmac.new(secret.encode(), PAIR_OF, hashlib.sha256).hexdigest()


def headers_of(moved: Dispatch) -> dict[str, str]:
    """The headers the hand-over leg carries: the org, the agent and the developer it is for."""
    values = {ORG: moved.org, AGENT: moved.agent, HOLDER: moved.holder}
    return {HEADER_OF[attribute]: value or "" for attribute, value in values.items()}


# The hand-over rule dispatches the same metadata for every ring; whose ring it is rides the leg.
def handed_in(dispatch: Dispatch, attributes: Mapping[str, str]) -> Dispatch:
    """The hand-over rule's dispatch made the developer's call, from what its leg carries."""
    if dispatch.diverted_from is None or dispatch.agent is not None or AGENT not in attributes:
        return dispatch
    carried = {
        "org": attributes.get(ORG),
        "agent": attributes.get(AGENT),
        "holder": attributes.get(HOLDER),
    }
    return dispatch.model_copy(update=carried)


# LiveKit ends no SIP leg when another leaves: a caller who hung up would leave the developer's
# copy talking to nobody, and a copy that ended would leave the caller in silence.
async def unbridged(server: api.LiveKitAPI, event: WebhookEvent) -> bool:
    """Close a room a hand-over bridges once either of its legs left; whether it did."""
    if event.event != LEFT or event.participant.kind != api.ParticipantInfo.Kind.SIP:
        return False
    name = event.room.name
    if not event.participant.identity.startswith(LEG_PREFIX):
        seats = await rooms.seats_in(server, name)
        if not any(seat.identity.startswith(LEG_PREFIX) for seat in seats):
            return False
    await rooms.room_closed(server, name)
    return True
