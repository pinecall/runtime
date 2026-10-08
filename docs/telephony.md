# Telephony — how a phone call reaches an agent

A phone call crosses three hands before an agent hears it: the **carrier** that owns the number,
the **SIP service** on the box that answers it, and the **gateway** that knows which agent it is
for. This page is how they fit, what any carrier must do to reach a box, and what the box does
for you in return. The doors themselves are [numbers.md](protocol/numbers.md); the operator's are
[operator-api.md](protocol/operator-api.md).

```
caller → carrier → INVITE to <box>:5060 → livekit-sip → room call-… → worker → GET /v1/routes → agent
                          │                    │
                    the fence (the cloud's the org's trunk (its numbers, the networks
                    firewall, Terraform's) it admits) and its world's dispatch rule
```

## Three planes

| plane | who | what it knows | what it never knows |
|---|---|---|---|
| the carrier | Twilio, Telnyx, a PBX, a national carrier | the number, and the SIP address its calls go to | the agent, the org's name |
| the SIP service | a `livekit-sip` per world, beside its world's LiveKit, on its node's own network (`infra/charts/pinecall/templates/sip.yaml`) | on its world's LiveKit, one inbound trunk per fence: the numbers of that world it admits and the networks it admits them from; one dispatch rule per org | the agent: a rule names the world's fleet, nothing more |
| the gateway | the box's gateway and Postgres | the `routes` row: org, number, agent, world, how it was written | the call's audio |

A call is dispatched to the fleet of its number's world; the worker that takes it asks the gateway
which agent answers the number (`GET /v1/routes?number=`), and opens the call.

## What any carrier must do

- Send the number's calls as SIP INVITEs to `sip:<number>@<the world's SIP name>:5060` (the
  box's name unless `PINECALL_SIP_DOMAIN` or `PINECALL_SANDBOX_SIP_DOMAIN` names another,
  [the-environment.md](the-environment.md)), over UDP or TCP
  (livekit-sip listens on both), with the number in E.164 (`+59829001199`) in the Request-URI.
- Send them from the signalling addresses it publishes, and nowhere else: the box admits a number
  from those networks alone.
- Send media to the box's RTP range, UDP 10000–10199, in one of the codecs livekit-sip offers:
  G.722, G.711 µ-law (PCMU) or G.711 A-law (PCMA), in that order of preference, and DTMF as
  telephone-events.

The box does not register anywhere: livekit-sip sends no REGISTER and answers one with `405`. A
carrier or PBX that only delivers calls to a registered endpoint needs a PBX in between that
registers to it and sends the calls on as above.

## The fence, twice

5060 is closed to the internet. Scanners flood an open 5060 within hours, and livekit-sip's own
flood guard then refuses the honest calls with the rest, so the box admits SIP from carriers'
signalling edges alone, twice:

1. **The cloud's firewall** in front of the SIP node (`infra/terraform/modules/edge`): 5060 open
   to the networks Terraform's `sip_sources` names, and denied to everyone else. The networks it
   should name are Twilio's, the other carriers' the operator admits and the addresses he
   approved, the list the console's box screen shows (`GET /v1/ops/carriers`, its `fence`); a
   change to it reaches the rule when the operator exports it for Terraform and applies it
   (`pinecall-runtime fence export`, [the-runtime-cli.md](the-runtime-cli.md)).
2. **The trunk on livekit-sip**: a number is admitted by one inbound trunk, which lists the
   networks it may come from. An INVITE for a number no trunk lists gets no answer at all
   (`hide_inbound_port`): a scanner learns nothing and opens no room.

One trunk per fence: `<org>` for Twilio's networks, `<org>:<carrier>` for a catalog carrier's,
`<org>:<peer>` for a SIP peer's (with its username and password), `<org>:<number>` for a number
hooked from networks of its own. Two trunks may never list one number: livekit-sip refuses the
INVITE, so the box refuses the second import before anything is written. A trunk that lists no
network would admit every source, so a fence with nothing approved is no trunk at all.

The two layers say a call came from Twilio, not from whose Twilio: Twilio's networks are every
Twilio customer's. So the gateway asks a third thing when a call opens. Twilio stamps every call
with the account it came from (`X-Twilio-AccountSid`, which livekit-sip puts on the leg as
`sip.twilio.accountSid`); the worker passes it on (`POST /v1/calls`, `carrier_account`), and a call
to a number the box bought that did not come from the box's account, or to one imported from an
org's Twilio account that did not come from that account, is refused (`403`). A number hooked or
typed has no account known here and is not asked; where the box sets `PINECALL_APPROVE_HOOKED`, a
hooked one waits for the operator's approval instead ([protocol/numbers.md](protocol/numbers.md)). A carrier other than Twilio stamps no account,
and a call from it is not asked either.

## Worlds

Each world has a LiveKit of its own (`LIVEKIT_URL`, `LIVEKIT_SANDBOX_URL`,
[the-environment.md](the-environment.md)), so a sandbox call never shares a machine with a
production one, and a number lives on its world's alone: its trunk and its org's rule of the world,
`<org>:production` on production's LiveKit, `<org>:sandbox` on the sandbox's, each sending to that
world's fleet. The trunks keep their names on both: they are two clusters. Moving a number between
worlds takes it off the old world's LiveKit (its trunk, emptied, goes with it) and admits it on the
new one's; its carrier keeps sending its calls to the SIP name it was pointed at. Where the sandbox
shares production's LiveKit (`LIVEKIT_SANDBOX_URL` unset), both rules are on the one LiveKit and a
number moves between the two lists, its trunk untouched. A rule always names its trunks: one that
named none would dispatch every trunk's calls.

## A developer's own phone: the hand-over

A developer who holds a sandbox copy of an agent and registered their own phone
(`PUT /v1/line/from`) reaches that copy when the phone calls one of the org's production numbers;
every other caller reaches production's agent. The call arrives as any other, on production's
LiveKit, and production's worker asks the gateway whose ring it is
(`GET /v1/agents/{slug}/rings-for`).

Where the worlds share one LiveKit, the worker sends the sandbox's fleet into the same room and
leaves. Where each has its own, the sandbox's workers never see production's rooms, so the ring
is carried across by SIP: production's room dials a leg to the sandbox's livekit-sip,
`sip:<the production number>@<the sandbox's SIP name>` over UDP, with the caller's number as its
From and three headers that say whose ring it is, `X-Pinecall-Org`, `X-Pinecall-Agent` and
`X-Pinecall-Holder`. Once the sandbox answers, production's worker leaves: the caller and the leg
stay bridged in production's room, the audio passing through production's media node, and the
agent runs on the sandbox's. A leg the sandbox does not answer within 10 s is given up, and the
caller reaches production's agent.

On the sandbox's LiveKit one trunk and one rule, both named `hand-over`, admit these legs. The
trunk lists no number, unlike an org's: it admits whatever production dials, from production's
media address alone (the address `PINECALL_SIP_DOMAIN` names, looked up at start: production's
livekit-sip sends from it) and with a username and password drawn from LiveKit's API secret, so
nothing new is kept. It turns the three headers into the leg's attributes; the rule sends the leg
to the sandbox's fleet as a ring diverted from production, and the gateway, offering the room,
makes it the developer's from those attributes: their corner, the org, the agent. The sandbox's
call is logged there like the in-room one: in the developer's corner, at the production number,
with the caller's number as the contact. Production keeps no log of it.

LiveKit hangs up neither leg when the other leaves, so the gateway does, from LiveKit's webhook:
when either leg of a room the hand-over bridges leaves, the room is deleted, which hangs up the
other. The caller hanging up ends the sandbox's call; the sandbox's agent ending its call hangs
up the caller.

The cloud's firewall in front of the sandbox's SIP node must admit 5060 from production's media
address too (Terraform's `sip_sources`, beside the carriers' networks); its RTP range is open to
every address already.

## The carrier catalog: what the operator admits

The box knows a catalog of carriers (`channels/telephony/carriers.csv`), each with the signalling
networks it publishes, the page they were read from and the day:

| carrier | how an org connects | read from |
|---|---|---|
| Twilio | automatic: the box drives its API | twilio.com/docs/sip-trunking/ip-addresses |
| Telnyx | guided: one address into its portal | sip.telnyx.com |
| Vonage | guided | Vonage's help center, article 360035471331 |
| Plivo | guided | plivo.com/docs/sip-trunking/concepts/technical-specifications |

Twilio is the box's own carrier and is admitted always. The operator admits the others (Box →
Carriers); an admitted one opens the fence to its networks within a minute, and every org sees it
when it adds a number. A carrier's list changes on its own page: the file is read again, with the
day, when it does.

## Twilio, through the box

With an org's Twilio account (`PUT /v1/carrier`, an API key the org can revoke any time), the box
does what a person would do in Twilio's console:

- lists the account's numbers, every page;
- finds the account's SIP trunk whose origination is `sip:<box>:5060;transport=udp`, makes it when
  there is none, and attaches the number to it (its voice URL is then ignored);
- buys a number, on the box's own account, for an org that has none;
- for calling out, sets the trunk's termination host (`<box>-<account>.pstn.twilio.com`), makes a
  credential list and one credential per org, whose password is minted here and kept sealed
  because Twilio shows it once.

Every step is looked up before it is written, so a second run writes nothing twice, and
`?dry_run=true` lists the steps without taking them. The box never deletes and remakes a trunk:
a carrier's fraud checks read that pattern as an account taken over.

## A carrier of the catalog, by hand

With a guided carrier the org keeps its numbers where they are and points them at the box in the
carrier's portal: a SIP connection (Telnyx: "FQDN"; Vonage: "SIP Connect") sending to
`<box>:5060` over UDP, authenticated by IP, with the number on it. In Pinecall it adds the number
with `POST /v1/numbers {hooked: true, via: "telnyx"}`; the box admits it from that carrier's
networks. The number's path says "waiting for the first call" until a call reaches the box.

## Your own PBX, or a carrier the box does not know

Asterisk, FreePBX, 3CX, a local carrier: the org declares a SIP peer (`PUT /v1/carrier {kind: "sip",
username, password, addresses, outbound_host?}`) and points a trunk of its PBX at the box with that
username and password. `addresses` are the networks the PBX calls from: each a public IPv4 address
or a network no wider than a `/24`, and each waits for the box's operator to approve it, once.
Until he does, the number is routed and on no trunk, and its path says what it waits for. A number
hooked from networks of its own (`POST /v1/numbers {hooked: true, networks}`) waits the same way.

## Calling out

The SIP service keeps no outbound trunk: each leg is dialled with its trunk inline, built from the
account of the number shown. A Twilio account dials its termination host with the org's credential;
a peer is dialled at its `outbound_host`, with its outbound pair or its inbound one. A number the
org hooked by hand, or one the box bought, dials through no account. Before any carrier is reached
the dial passes the org's guards ([numbers.md](protocol/numbers.md), "Dialling out"). A warm
transfer and `room.invite` are a second leg of the same kind.

## What is not done yet

- **TLS and SRTP.** The box speaks SIP over UDP and TCP. livekit-sip takes TLS on 5061 with a `tls:`
  block in `sip.yaml` (its certificates) and SRTP per trunk (`media.encryption`); neither is set on
  the box today, and a carrier that requires them cannot reach it yet.
- **Number porting, emergency calls, SMS and caller-name lookup** are the carrier's, not the box's.
- **A carrier's own limits** (calls per second, calls at once on a trunk) are the carrier's account's.

## At start, and at scale

LiveKit keeps its trunks and rules in Redis, which can be emptied; Postgres is the truth. At start
the gateway admits every routed number again on its world's LiveKit with the fence it has now,
takes a number off its org's trunks when nothing approved fences it, takes it off the other world's
LiveKit where it is still listed (one admitted before the worlds had a LiveKit each), deletes
nothing else, and never touches a carrier. Where the sandbox has a LiveKit of its own and
production's SIP a name of its own, it makes or mends the sandbox's `hand-over` trunk and rule.
The operator's approval or refusal of a network does the same for that org at once.

One livekit-sip holds 200 calls (its RTP range). A second, on the box's Redis, answers every
number the box routes too, and the carrier spreads calls across both with a second origination
URI; the chart runs one.

## Pricing a leg

A phone leg is priced from `infra/seed/prices.csv`, by the longest prefix of
`<carrier>-<inbound|outbound>/<number>`, minutes begun billed whole. The prices carried are
Twilio's; a leg through another carrier is recorded with its minutes and priced at nothing until
its rows are added.
