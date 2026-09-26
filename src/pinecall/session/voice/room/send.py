"""room.send: the tenant's payload to a browser in the room, over the DataChannel."""

from __future__ import annotations

from pinecall.session.voice.room.room_handle import Holding
from pinecall_protocol.commands import RoomSend
from pinecall_protocol.room import RoomSent

VERB = "room.send"


# The payload never enters the log: the tenant chose what to send and to whom, and room.sent
# keeps the topic, the addressee and the size, which is what a reader needs to see it happened.
async def sent(holding: Holding, wanted: RoomSend) -> None:
    """publish_data on the topic, to one identity or to the room, then room.sent."""
    to = () if wanted.to is None else (wanted.to,)
    try:
        size = await holding.publish(wanted.topic, wanted.data, to)
    except Exception as refused:
        holding.failed(VERB, str(refused))
        return
    holding.writing.later("room.sent", RoomSent(topic=wanted.topic, to=wanted.to, bytes=size))
