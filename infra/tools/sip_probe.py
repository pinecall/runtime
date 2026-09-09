"""`sip_probe.py`: one INVITE at the box, and the one question it answers — did a room appear?"""

import argparse
import asyncio
import sys
import time

from livekit import api
from sip_call import SipCall

# The SFU creates the room off the ACK, not off the 200: give it a moment before asking.
SETTLE_S = 3.0

# Long enough for the worker to join and say its first line, short enough to be a probe.
HOLD_S = 20.0

DROPPED = (
    "no final response: the INVITE was dropped, which is what the box does to an address the\n"
    "allow-list does not carry (the fence's `carrier_signalling` set) and to a number no\n"
    "inbound trunk declares. Add THIS machine's address to the trunk for the run, then remove it."
)


def main(argv: list[str]) -> int:
    """Call once, ask the SFU what happened, hang up. Exit 0 only when a room appeared."""
    arguments = parse_arguments(argv)
    call = SipCall(arguments.host, arguments.domain, arguments.dialled, arguments.caller)
    print(f"→ INVITE sip:{arguments.dialled}@{arguments.domain} from {call.where_it_calls_from}")

    started = time.time()
    answer = call.invite()
    if answer is None:
        print(DROPPED, file=sys.stderr)
        return 1
    if not answer.startswith("SIP/2.0 2"):
        print("the box refused the call — the line above is its reason", file=sys.stderr)
        return 1

    call.acknowledge(answer)
    time.sleep(SETTLE_S)
    rooms = asyncio.run(rooms_since(started))
    print(f"→ holding {HOLD_S:.0f}s: the agent is in the room by now, and nobody is listening")
    time.sleep(HOLD_S)
    call.hang_up()
    return _report(rooms)


async def rooms_since(started: float) -> list[str]:
    """Every room the SFU opened after the INVITE went out: the proof the whole path ran."""
    async with api.LiveKitAPI() as livekit:
        rooms = (await livekit.room.list_rooms(api.ListRoomsRequest())).rooms
        return [room.name for room in rooms if room.creation_time >= int(started)]


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    """Where the box is, what to dial, and who to be while dialling it."""
    parser = argparse.ArgumentParser(description="Send one SIP INVITE and say what came back.")
    parser.add_argument("--host", required=True, help="the box's address, e.g. 34.58.189.131")
    parser.add_argument("--domain", required=True, help="its SIP name, e.g. box.example")
    parser.add_argument("--dialled", required=True, help="the number in the To: header, E.164")
    parser.add_argument("--caller", default="+59899000000", help="the number in the From: header")
    return parser.parse_args(argv)


def _report(rooms: list[str]) -> int:
    """A room is the acceptance criterion; the audio is the one thing only a phone can prove."""
    if not rooms:
        print("answered, but the SFU opened no room: read `docker logs pinecall-sip-1`")
        return 1
    for name in rooms:
        print(f"room     {name}")
    print("the phone path is up: trunk → rule → room → fleet. Only the audio needs a phone.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
