"""call.callback as the log keeps it: who is asking to be rung back, from the call they are on."""

from __future__ import annotations

from dataclasses import asdict

from pinecall.types import CallContext, Contact
from pinecall_protocol import defs
from pinecall_protocol.commands import CallCallback
from pinecall_protocol.events import CallbackRequested

# What `via` says about a request the agent itself took, beside the widget's and the overflow
# agent's: this one was asked for out loud, on a call somebody was already having.
BY_THE_AGENT = "agent"


def a_callback(context: CallContext, wanted: CallCallback) -> CallbackRequested:
    """The entry: the number, what it is about, and the call and contact it was asked on."""
    return CallbackRequested(
        channel=context.channel,
        number=wanted.number,
        via=BY_THE_AGENT,
        call=context.call,
        contact=_on_the_wire(context.contact),
        when=wanted.when,
        note=wanted.note,
    )


# The platform's own Contact and the wire's carry the same five fields by the same names; the
# dataclass is what a session holds and the model is what an entry is written from.
def _on_the_wire(contact: Contact | None) -> defs.Contact | None:
    """Who is on the line, as an entry names them."""
    return None if contact is None else defs.Contact(**asdict(contact))
