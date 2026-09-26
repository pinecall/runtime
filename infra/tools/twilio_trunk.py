"""`twilio_trunk.py`: one number wired to the box, in writes made once and never a delete."""

import argparse
import asyncio
import sys
from typing import Any

from carrier_cidrs import signalling_cidrs
from livekit import api
from twilio_rest import ACCOUNTS_API, TRUNKING_API, Twilio, TwilioRefused

# The name the dispatch rule asks for is the name the worker joins the media plane under, so its
# default is imported from the package both processes hold rather than spelled again here: a rule
# naming a worker nobody registered rings forever. An instance with a fleet of its own (the
# sandbox's, PINECALL_FLEET) passes --fleet with that name.
from pinecall.routes.twilio import BOX_TRUNK as TRUNK_NAME
from pinecall.routes.twilio import ORIGINATION_NAME, origination_uri
from pinecall.types.dispatch import DEFAULT_FLEET

LIVEKIT_TRUNK_NAME = "pinecall-inbound"
LIVEKIT_RULE_NAME = "pinecall-one-room-per-caller"

# One room per caller, named so a person reading the SFU's room list knows what they are looking at.
ROOM_PREFIX = "call-"

# The port and the transport are written down (routes/twilio.py, `origination_uri`) rather than
# left to the carrier to discover: the box publishes 5060 for UDP and TCP
# (`infra/box/containers/pinecall-sip.container`), livekit-sip listens there and nowhere else
# (`infra/box/sip.yaml`, `sip_port: 5060`), there is no TLS listener on 5061, and the fence opens
# exactly that pair. A bare hostname sends Twilio looking for NAPTR and SRV records the box does
# not publish, and what comes back from that is a carrier's default, not ours.

# What the operator is told when the trunk is already there. A second trunk with the same purpose
# is how a number ends up attached to the one nobody is watching, and a delete-and-recreate loop is
# the exact shape a carrier's fraud detection reads as an account takeover — so this script does
# neither, and stops.
ALREADY_THERE = (
    "this script creates a trunk once and never touches one that exists: nothing was created.\n"
    "         to wire a second number, attach it to this trunk in the console;\n"
    "         to repoint the one standing there at this box, pass --adopt;\n"
    "         to build a different trunk, pass --trunk-name <another name>."
)


def main(argv: list[str]) -> int:
    """Print the plan, or make it: the carrier's trunk, then the SFU's side of the same call."""
    arguments = parse_arguments(argv)
    if arguments.dry_run:
        for line in the_plan(arguments):
            print(line)
        return 0
    try:
        return wire(Twilio.from_environment(), arguments)
    except KeyError as missing:
        print(f"missing credential: {missing}", file=sys.stderr)
        return 2
    except TwilioRefused as refused:
        print(str(refused), file=sys.stderr)
        return 1


# --dry-run opens no socket at all, so it needs no credential and can be read before anything of
# ours has ever spoken to the account: every line below is a request this run would make.
def the_plan(arguments: argparse.Namespace) -> list[str]:
    """Every API call the real run would make, in order, and none of them made."""
    carrier = adoption_plan(arguments) if arguments.adopt else creation_plan(arguments)
    return [f"number   {arguments.number}", *carrier, *livekit_plan(arguments)]


def creation_plan(arguments: argparse.Namespace) -> list[str]:
    """A trunk this account does not have yet, built from nothing."""
    return [
        f"GET      {TRUNKING_API}/Trunks?PageSize=50",
        (
            "         → a trunk named "
            f"{arguments.trunk_name!r} standing there ends the run, creating nothing"
        ),
        (
            f"GET      {ACCOUNTS_API}/Accounts/<account>/IncomingPhoneNumbers.json"
            f"?PhoneNumber={arguments.number}"
        ),
        f"POST     {TRUNKING_API}/Trunks  FriendlyName={arguments.trunk_name}",
        (
            f"POST     {TRUNKING_API}/Trunks/<trunk>/OriginationUrls  "
            f"SipUrl={origination_uri(arguments.sip_host)}"
        ),
        f"POST     {TRUNKING_API}/Trunks/<trunk>/PhoneNumbers  PhoneNumberSid=<number>",
    ]


def adoption_plan(arguments: argparse.Namespace) -> list[str]:
    """A trunk that already owns the number, moved to this box in exactly one write."""
    return [
        f"GET      {TRUNKING_API}/Trunks?PageSize=50",
        f"         → no trunk named {arguments.trunk_name!r} ends the run: --adopt never creates",
        f"GET      {TRUNKING_API}/Trunks/<trunk>/PhoneNumbers",
        f"         → {arguments.number} not on that trunk ends the run, attaching nothing",
        f"GET      {TRUNKING_API}/Trunks/<trunk>/OriginationUrls",
        "         → two of them end the run: which one is this box is a person's call",
        (
            f"POST     {TRUNKING_API}/Trunks/<trunk>/OriginationUrls/<uri>  "
            f"SipUrl={origination_uri(arguments.sip_host)}"
        ),
        "         → and that is the only write --adopt makes: no trunk, no number, no delete",
    ]


def livekit_plan(arguments: argparse.Namespace) -> list[str]:
    """The SFU's half, identical whichever way the carrier's trunk got here."""
    return [
        (
            "livekit  ListSIPInboundTrunk / ListSIPDispatchRule  "
            "→ one of ours standing is used as it is, never doubled"
        ),
        f"livekit  CreateSIPInboundTrunk  {LIVEKIT_TRUNK_NAME}  numbers=[{arguments.number}]",
        f"         allowed_addresses={','.join(signalling_cidrs())}",
        (
            f"livekit  CreateSIPDispatchRule  {LIVEKIT_RULE_NAME}  "
            f"room {ROOM_PREFIX}*  agent {arguments.fleet}"
        ),
    ]


def wire(twilio: Twilio, arguments: argparse.Namespace) -> int:
    """The carrier first: without a trunk that owns the number there is nothing to dispatch."""
    print(f"number   {arguments.number}")
    trunk_sid = carrier_trunk(twilio, arguments)
    if trunk_sid is None:
        return 0
    asyncio.run(livekit_side(arguments))
    print(f"\nroute    pinecall-runtime routes add {arguments.number} <agent>")
    return 0


def carrier_trunk(twilio: Twilio, arguments: argparse.Namespace) -> str | None:
    """The trunk that owns the number: adopted, created, or left exactly as it stands."""
    standing = _named(twilio.get(f"{TRUNKING_API}/Trunks?PageSize=50")["trunks"], arguments)
    if arguments.adopt:
        return adopted_trunk(twilio, standing, arguments)
    if standing is not None:
        print(f"trunk    {standing['sid']}  {standing['friendly_name']}  — already there")
        report_transfer(standing)
        print(f"         {ALREADY_THERE}")
        return None
    return created_trunk(twilio, arguments)


def created_trunk(twilio: Twilio, arguments: argparse.Namespace) -> str | None:
    """A trunk, its origination URI and the number attached to it — the account had none."""
    number = number_row(twilio, arguments.number)
    if number is None:
        print(f"number   {arguments.number} is not on this Twilio account", file=sys.stderr)
        return None

    trunk = twilio.post(f"{TRUNKING_API}/Trunks", {"FriendlyName": arguments.trunk_name})
    trunk_sid = str(trunk["sid"])
    print(f"trunk    {trunk_sid}  {trunk['friendly_name']}  — created")
    report_transfer(trunk)

    origination = twilio.post(
        f"{TRUNKING_API}/Trunks/{trunk_sid}/OriginationUrls", origination_form(arguments.sip_host)
    )
    print(f"origin   {origination['sid']}  {origination_uri(arguments.sip_host)}")

    attached = twilio.post(
        f"{TRUNKING_API}/Trunks/{trunk_sid}/PhoneNumbers", {"PhoneNumberSid": str(number["sid"])}
    )
    print(f"attached {attached['sid']}  the trunk owns the call now: the number's voice_url is not")
    return trunk_sid


# A trunk somebody else built can already own the number and be wrong in one field — the address it
# sends the INVITE to. Building a second trunk for that is how a number ends up on the one nobody
# is watching, so --adopt moves the field and creates nothing: no trunk, no number, no delete.
def adopted_trunk(
    twilio: Twilio, standing: dict[str, Any] | None, arguments: argparse.Namespace
) -> str | None:
    """A trunk that already exists, repointed at this box — or a refusal that changes nothing."""
    if standing is None:
        print(
            f"trunk    no trunk named {arguments.trunk_name!r} on this account:"
            " --adopt adopts, it never creates",
            file=sys.stderr,
        )
        return None
    trunk_sid = str(standing["sid"])
    print(f"trunk    {trunk_sid}  {standing['friendly_name']}  — adopting")
    report_transfer(standing)
    if not carries(twilio, trunk_sid, arguments.number):
        print(
            f"number   {arguments.number} is not on trunk {trunk_sid}: attach it first, in the"
            " console, and run this again",
            file=sys.stderr,
        )
        return None
    return trunk_sid if repointed(twilio, trunk_sid, arguments) else None


# One origination URI is a trunk with one answer to "where do I send this call". Two is a choice,
# and a script that picks for you is a script that silently moves the wrong one.
def repointed(twilio: Twilio, trunk_sid: str, arguments: argparse.Namespace) -> bool:
    """The trunk's one origination URI, moved to this box, printed before and after."""
    urls = f"{TRUNKING_API}/Trunks/{trunk_sid}/OriginationUrls"
    standing: list[dict[str, Any]] = twilio.get(urls)["origination_urls"]
    for uri in standing:
        print(f"origin   {uri['sid']}  {uri['friendly_name']}  {uri['sip_url']}  — before")
    if len(standing) > 1:
        print(
            "origin   this trunk carries more than one origination URI, and which of them is this"
            " box is a person's call: nothing was moved",
            file=sys.stderr,
        )
        return False
    form = origination_form(arguments.sip_host)
    where = urls if not standing else f"{urls}/{standing[0]['sid']}"
    moved = twilio.post(where, form)
    made = "— created, the trunk carried none" if not standing else "— after"
    print(f"origin   {moved['sid']}  {moved['friendly_name']}  {moved['sip_url']}  {made}")
    return True


def carries(twilio: Twilio, trunk_sid: str, number: str) -> bool:
    """Whether the number is on this trunk. Nothing here attaches one, and nothing detaches one."""
    on_it: list[dict[str, Any]] = twilio.get(f"{TRUNKING_API}/Trunks/{trunk_sid}/PhoneNumbers")[
        "phone_numbers"
    ]
    for row in on_it:
        print(f"number   {row['sid']}  {row['phone_number']}  — on the trunk, left as it is")
    return any(row.get("phone_number") == number for row in on_it)


async def livekit_side(arguments: argparse.Namespace) -> None:
    """The inbound trunk that admits the number, and the rule that opens a room per caller."""
    async with api.LiveKitAPI() as livekit:
        trunk_id = await inbound_trunk(livekit, arguments)
        await one_room_per_caller(livekit, trunk_id, arguments)


# Both halves are looked for by name before they are made. Two inbound trunks declaring one number,
# or two rules on one trunk, is a call landing in whichever the SFU happened to read first — and a
# second run of this script is a thing that happens the moment the first one is interrupted.
async def inbound_trunk(livekit: api.LiveKitAPI, arguments: argparse.Namespace) -> str:
    """The trunk that admits this number from the carrier's networks, made once."""
    standing = await livekit.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
    for trunk in standing.items:
        if trunk.name == LIVEKIT_TRUNK_NAME:
            print(f"lk trunk {trunk.sip_trunk_id}  numbers={list(trunk.numbers)}  — already there")
            return trunk.sip_trunk_id
    made = await livekit.sip.create_inbound_trunk(
        api.CreateSIPInboundTrunkRequest(
            trunk=api.SIPInboundTrunkInfo(
                name=LIVEKIT_TRUNK_NAME,
                numbers=[arguments.number],
                # The second fence, independent of the firewall: livekit-sip drops an INVITE
                # from anywhere else without a word, which is what a scanner should learn.
                allowed_addresses=signalling_cidrs(),
            )
        )
    )
    print(f"lk trunk {made.sip_trunk_id}  numbers={list(made.numbers)}  — created")
    return made.sip_trunk_id


async def one_room_per_caller(
    livekit: api.LiveKitAPI, trunk_id: str, arguments: argparse.Namespace
) -> None:
    """The rule that gives every caller a room of their own, with the fleet dispatched into it."""
    standing = await livekit.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())
    for rule in standing.items:
        if rule.name == LIVEKIT_RULE_NAME:
            print(f"lk rule  {rule.sip_dispatch_rule_id}  trunks={list(rule.trunk_ids)}  — there")
            return
    made = await livekit.sip.create_dispatch_rule(dispatch_rule(trunk_id, arguments))
    print(f"lk rule  {made.sip_dispatch_rule_id}  room {ROOM_PREFIX}*  agent {arguments.fleet}")


# The rule names a fleet, never a tenant: which agent answers a number is one row in the routes
# table (`pinecall-runtime routes add`), and moving a number is not a LiveKit change at all.
def dispatch_rule(trunk_id: str, arguments: argparse.Namespace) -> api.CreateSIPDispatchRuleRequest:
    """One room per caller, with the fleet dispatched into it when the room is created."""
    return api.CreateSIPDispatchRuleRequest(
        name=LIVEKIT_RULE_NAME,
        trunk_ids=[trunk_id],
        rule=api.SIPDispatchRule(
            dispatch_rule_individual=api.SIPDispatchRuleIndividual(room_prefix=ROOM_PREFIX)
        ),
        room_config=api.RoomConfiguration(
            agents=[api.RoomAgentDispatch(agent_name=arguments.fleet)]
        ),
    )


# Whether a cold transfer works at all is the carrier's decision, and it costs money on every
# transfer, so this script reads the two properties and prints the command — a human runs it.
def report_transfer(trunk: dict[str, Any]) -> None:
    """Say whether this trunk carries a SIP REFER, and how to turn it on without this script."""
    mode = trunk.get("transfer_mode", "?")
    caller_id = trunk.get("transfer_caller_id", "?")
    reading = "REFER to the PSTN allowed" if mode == "enable-all" else "a cold transfer will fail"
    print(f"transfer {mode} / caller-id {caller_id}  →  {reading}")
    if mode == "enable-all":
        return
    print("         turn it on yourself — the exact call, never run from here:")
    print(f"           twilio api trunking v1 trunks update --sid {trunk.get('sid', '<trunk>')} \\")
    print("             --transfer-mode enable-all --transfer-caller-id from-transferee")


def number_row(twilio: Twilio, number: str) -> dict[str, Any] | None:
    """The account's row for one E.164 number, or nothing when the account does not own it."""
    numbers = f"{ACCOUNTS_API}/Accounts/{twilio.account_sid}/IncomingPhoneNumbers.json"
    rows: list[dict[str, Any]] = twilio.get(f"{numbers}?PhoneNumber={number}")[
        "incoming_phone_numbers"
    ]
    return rows[0] if rows else None


def origination_form(sip_host: str) -> dict[str, str]:
    """The body of every write of an origination URI, so create and adopt cannot disagree."""
    return {
        "FriendlyName": ORIGINATION_NAME,
        "SipUrl": origination_uri(sip_host),
        "Weight": "10",
        "Priority": "10",
        "Enabled": "true",
    }


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    """The number and the box; everything else has one right answer for this repo."""
    parser = argparse.ArgumentParser(description="Wire a Twilio number into this box's LiveKit.")
    parser.add_argument("--number", required=True, help="E.164, e.g. +59829000000")
    parser.add_argument("--sip-host", required=True, help="the box's public name, e.g. box.example")
    parser.add_argument("--trunk-name", default=TRUNK_NAME, help=f"default {TRUNK_NAME}")
    parser.add_argument("--fleet", default=DEFAULT_FLEET, help=f"default {DEFAULT_FLEET}")
    parser.add_argument(
        "--adopt",
        action="store_true",
        help="move a standing trunk's one origination URI here; create nothing",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan, call nothing")
    return parser.parse_args(argv)


def _named(trunks: list[dict[str, Any]], arguments: argparse.Namespace) -> dict[str, Any] | None:
    """The trunk this run would have created, if the account already carries it."""
    return next((t for t in trunks if t.get("friendly_name") == arguments.trunk_name), None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
