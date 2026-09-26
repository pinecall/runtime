"""The doors a call comes through and the direction it took: the closed sets the contracts share."""

from typing import Literal, get_args

# phone rides SIP into a LiveKit room, web is the widget over WebRTC, whatsapp is text.
type Channel = Literal["phone", "web", "whatsapp"]

# inbound: the public reached the agent. outbound: the agent dialled out.
type Direction = Literal["inbound", "outbound"]

CHANNELS: frozenset[str] = frozenset(get_args(Channel.__value__))
DIRECTIONS: frozenset[str] = frozenset(get_args(Direction.__value__))

# A number answers on the phone and on WhatsApp; the widget needs none.
CHANNELS_WITH_A_NUMBER: frozenset[str] = frozenset({"phone", "whatsapp"})

# Nothing was dialled: a browser opened the widget and joined the room. The worker's router and
# the token door both name it, so it is spelled here and not in either.
THE_WIDGET: Channel = "web"
