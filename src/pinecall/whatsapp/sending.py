"""What the agent says on a thread, put back on the wire: one watcher of one call's log."""

from __future__ import annotations

import logging

from pinecall.log.entry import Entry
from pinecall.session.text.session import TextSession, Watcher
from pinecall.whatsapp.graph import Graph, GraphRefused
from pinecall_protocol.events import ErrorEvent

logger = logging.getLogger(__name__)

# The one entry the contact ever receives. Not agent.transcript: those are the deltas of a turn as
# it forms, and a person on WhatsApp must not watch a sentence being typed four times over.
A_TURN = "turn.agent"

# Meta would not take it. The entry goes in the call's own log so a desk reading the thread sees
# the message that did not go out, and the thread stays open: the next turn may well go through.
NOT_SENT = "whatsapp_not_sent"

# The line the process's own log gets. It names the call and never the token or the text: a
# refusal is an operator's problem, not a place to print somebody's conversation.
REFUSED = "whatsapp: the agent's turn on call %s did not go out: %s"


# The wire is fed FROM the log rather than from the model, because the log is the truth: whatever
# wrote a turn.agent — the agent's own answer, agent.say, or a supervisor's `say` while they hold
# the thread — reaches the contact by this one path, and nothing has to remember to send it too.
def sending(
    session: TextSession, graph: Graph, token: str, phone_number_id: str, to: str
) -> Watcher:
    """Every turn the agent takes on this thread, out through the Graph API, in the log's order."""

    async def send(entry: Entry) -> None:
        said = entry.data.get("text")
        if entry.type != A_TURN or not isinstance(said, str) or not said:
            return
        try:
            await graph.send_text(token, phone_number_id, to, said)
        except GraphRefused as refused:
            logger.warning(REFUSED, session.call, refused)
            await session.emit(
                "error", ErrorEvent(code=NOT_SENT, message=str(refused), recoverable=True)
            )

    return send
