"""call.dtmf: touch tones down the caller's own leg, for an IVR listening on the far end."""

from __future__ import annotations

import asyncio

from pinecall.session.voice import sip
from pinecall.session.voice.room.holding import Holding
from pinecall_protocol.commands import CallDtmf

VERB = "call.dtmf"

# RFC 4733's own numbering, which is what livekit's publish_dtmf takes: the digits are themselves,
# then the two keys a phone has that are not digits.
CODES: dict[str, int] = {**{str(digit): digit for digit in range(10)}, "*": 10, "#": 11}

# A comma is the pause an IVR needs between a menu and the number it asked for, spelled the way
# every dialler spells it. Everything else is refused whole: half a card number is worse than none.
PAUSE = ","
PAUSE_S = 0.5

# Tones sent back to back arrive as one long press on some carriers, so they are spaced.
BETWEEN_TONES_S = 0.12

NO_LEG = "call.dtmf: this call has no phone leg, so there is nothing to send tones down"
NOT_A_TONE = "call.dtmf: {digit!r} is not a touch tone (0-9, * or #), and nothing was sent"


async def sent(holding: Holding, wanted: CallDtmf) -> None:
    """Every tone in order, down the caller's leg. One that is not a tone refuses the whole lot."""
    for digit in wanted.digits:
        if digit != PAUSE and digit not in CODES:
            holding.failed(VERB, NOT_A_TONE.format(digit=digit))
            return
    if await sip.the_sip_leg(holding.room, holding.channel) is None:
        holding.failed(VERB, NO_LEG)
        return
    for digit in wanted.digits:
        if digit == PAUSE:
            await asyncio.sleep(PAUSE_S)
            continue
        try:
            await holding.room.local_participant.publish_dtmf(code=CODES[digit], digit=digit)
        except Exception as refused:  # noqa: BLE001 — every way the server says no is the same
            holding.failed(VERB, str(refused))
            return
        await asyncio.sleep(BETWEEN_TONES_S)
