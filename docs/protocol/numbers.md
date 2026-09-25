# Numbers — a tenant's own carrier, its numbers imported

Until 2026-09-12 a number reached the box through the one trunk an operator wired by hand with
`infra/tools/twilio_trunk.py`: the box's Twilio, the box's trunk, `routes add` after. A tenant with
numbers of its own — a Twilio account, or a carrier or a PBX that speaks SIP — has the gateway do
that wiring for them, on THEIR account, from the console's Numbers screen. Every door below takes
the org's key with the `numbers` scope.

## The carrier — `PUT /v1/carrier`

```json
{ "kind": "twilio", "account_sid": "AC…", "user": "SK…", "secret": "…" }
{ "kind": "sip", "username": "pbx", "password": "…", "addresses": ["203.0.113.0/24"],
  "outbound_host": "sip.carrier.example:5060", "outbound_transport": "tls",
  "outbound_username": "pinecall-out", "outbound_password": "…" }
```

One per org, replaced whole, sealed under `PINECALL_VAULT_KEY` exactly as a provider key is (a
runtime with no vault key answers `503` in the vault's sentence). A Twilio account is opened once
to check — `400 Twilio refused these credentials` otherwise; `user` is an API key SID or the account
SID again with the auth token. `GET /v1/carrier` answers `{kind, account}` and never a secret;
`DELETE` forgets it, and the numbers already imported stay routed until each is let go.

The four `outbound_*` fields on a SIP peer are optional and answer the OTHER direction — where the
box places an INVITE, rather than where one arrives from. `addresses` is the fence the box opens
for calls the peer SENDS; an address a carrier sends from is not one it accepts a call at, so a
peer that declares no `outbound_host` can be called FROM and never dialled THROUGH, and the
outbound doors say that by name rather than guessing. `outbound_transport` is `auto` (the default,
letting the SFU choose), `udp`, `tcp` or `tls`. `outbound_username` and `outbound_password` are what
the box authenticates as when it calls the peer; unsaid, they are the pair the peer already
registers with, because one account in both directions is what most carriers sell. A username
given without a password is refused `400`.

## What the account owns — `GET /v1/numbers/available`

`{kind: "twilio", numbers: [{number, name, imported}]}`: every number the account owns, and whether
this org already imported it in this world. A SIP peer owns what it owns and nobody here can list
it: `{kind: "sip", numbers: []}`, and the import takes the number typed.

## Importing one — `POST /v1/numbers {number, agent, channel?}`

Three writes, each looked up before it is made and named in the answer's `steps`, so a second run
of an interrupted import creates nothing twice and nothing is ever deleted:

1. **the carrier's trunk** (Twilio only): a trunk named `<fleet>-<org>` on the tenant's account,
   created once; its one origination URI set to the box — `sip:<PINECALL_DOMAIN>:5060;transport=udp`,
   the port livekit-sip listens on and the fence opens; the number attached to it. A trunk carrying
   two origination URIs is a person's call and refused: nothing is moved.
2. **the SFU's trunk**: one LiveKit inbound trunk per org, `<fleet>:<org>`, with the number added
   to its allow-list; Twilio's signalling networks as `allowed_addresses` (the very set the fence
   opens, held equal by a test), or a SIP peer's own addresses and its username and password; and
   one dispatch rule, `<fleet>:<org>:one-room-per-caller`, the instance's fleet dispatched into it.
3. **the route**: `(org, number) → agent`, on `channel` (`phone` or `whatsapp`), in the key's world
   — the row `routes add` writes, so moving the number later is a route change and no wiring.

`<fleet>` is the instance's `PINECALL_FLEET` (`pinecall` unless set). Two instances share one SFU
and every trunk is found **by name** across all of it, so each instance's names lead with its own
fleet; the colon is the separator because no org id or slug can hold one, so no fleet and org can
ever read as another pair. A trunk or rule the default fleet made before the names carried it
(`pinecall-<org>`, `pinecall-<org>-one-room-per-caller`, `pinecall-<org>-out`) is found under that
name and renamed in place — the same id, its numbers untouched — by the next import or the
reconcile at startup, so no two trunks ever list one number. On the tenant's own Twilio account
the name is `<fleet>-<org>`, a dash: the default fleet's is the `pinecall-<org>` every production
trunk already has, and the sandbox's `pinecall-sandbox-<org>` stands beside it (`routes/twilio.py`).

`?dry_run=true` answers the same `steps` with the ids that stand today and writes nothing: what a
person reads before letting the gateway touch a carrier account. Refusals: `404` no carrier yet, or
a number the account does not own; `400` a channel with no number, a number that is not E.164;
`409` a number another inbound trunk on the SFU already lists — the other instance's, or one under
an old name — because livekit-sip refuses an INVITE two trunks list, and importing it would silence
it everywhere: the sentence names that trunk, and nothing is written anywhere; `503` no
`PINECALL_DOMAIN`, or no LiveKit pair on this gateway; `502` Twilio's own sentence.

## A number is one instance's

There is no door that moves a number to the other world. Each world is an instance of its own —
its own gateway, database and worker — and a number is imported into one of them, whose routes,
trunk and fleet answer it; the carrier lists one pool for both, and the import refuses a number the
other instance already carries (above). To try an agent on the real line, nobody moves anything:
the developer says which phone is theirs and holds the agent in the sandbox (next).

Whose corner a ring lands in, once a world is answering it, is the caller's phone and then the
**line** — [gateway-api.md](gateway-api.md) §3.

**A number in production still reaches a developer's own phone's sandbox copy.** One developer
testing needs no number of their own: `pinecall line from <their phone>` and `pinecall start`, and a
call from that phone to the production number is handed to their copy; every other caller reaches
production. The trunk and the rule stay production's. Production's worker asks its gateway (`GET
/v1/agents/{slug}/rings-for?caller=&org=`) about every phone call to a production number that no
dispatch aimed; production answers from its own claims, and when it has none asks its sandbox the
same door on the fleet key the sandbox minted for it (`PINECALL_SANDBOX_URL`, `PINECALL_SANDBOX_KEY`).
The answer is `{holder, fleet}`: the developer, and the fleet that holds their corner. The worker
then **hands the room over** instead of answering it — `create_dispatch` into the same room, to
that fleet, with the metadata any dispatch carries (`org`, `env: sandbox`, `holder`, `agent`,
`caller`, `direction`) and `diverted_from: production` — and ends its own job before anything of
the call was opened. The caller's SIP leg is a participant of its own and stays through both; the
sandbox's worker takes the dispatch like any other, builds the call on the number it rang, in that
developer's corner, and writes the sandbox's log. A sandbox that does not answer within two
seconds, or refuses, leaves the call in production (one WARNING line), as does a dispatch the SFU
refuses, and as does that phone the moment the developer stops holding the agent.

Which numbers those are, a developer's key cannot read off `GET /v1/numbers`: that door answers the
key's own world, to a key that opens `numbers`. `GET /v1/line/numbers` (`app`, a key naming a
person, the sandbox) answers `{calling, numbers}` — the phones this person said are theirs, and
the org's production phone numbers with the agent each reaches — and nothing else about a route.
The rows are production's, so the sandbox reads them at production (`GET /v1/routes?org=&env=
production`) on the fleet key production minted for it (`PINECALL_PEER_KEY`): `503` naming `box
peer` while it holds none, `502` when production does not answer. It is what the sandbox console's
Phone testing screen reads.

## Letting one go — `DELETE /v1/numbers/{number}`

The route removed and the number off the org's SFU trunk. The carrier account is not touched: the
number stays on the tenant's trunk, theirs to reattach or move in their own console. `404` for a
number this org never imported in this world.

## Buying one — `POST /v1/numbers/buy {country, area_code?, agent, channel?}`

For an org with no carrier of its own: a number bought on the **box's** Twilio account and billed
to the box, then wired exactly as an import is. The gateway needs `TWILIO_ACCOUNT_SID` and
`TWILIO_API_SECRET` (an API key's secret, with `TWILIO_API_KEY`; or the auth token) — the same
three names `infra/tools/twilio_trunk.py` reads — or the door answers `503` and says to bring a
carrier and import instead. It is **production's**: on a sandbox instance it is `404`, `this door
is production's: numbers are bought at <PINECALL_ELSEWHERE_URL>`, before anything is asked — the
box's account and the box's trunk are production's, and whether a sandbox may spend them is not
decided yet. The steps, in the answer's `steps`:

1. **buy**: Twilio's own search for one local, voice-capable number in that ISO country and area
   code (`404` when it has none there); a dry run names the number it found and pays for nothing.
2. **the box's trunk**: the trunk named `pinecall` on the box's account — the one `twilio_trunk.py`
   wires for an operator — created once if the box has none, its origination URI the box, the
   number attached.
3. **the SFU's trunk** and 4. **the route**, as an import: the org's own LiveKit inbound trunk
   `<fleet>:<org>` admits the number from Twilio's networks, and the route is written with
   `managed: true`.

What the box buys is a **stock the plan caps**: the `numbers` quota (`PUT /v1/ops/orgs/{org}/quotas`)
is measured on the org's managed routes alone — a number the tenant imported from its own account
counts against nothing — and the door answers `429 org <org> has used 1 of its 1 numbers:
credits.exhausted` before Twilio is asked, `0` meaning the plan includes none. Letting a managed
number go (`DELETE /v1/numbers/{number}`) makes room again; the number itself stays on the box's
account, the operator's to release there.

## The other trunk — `GET` · `POST /v1/carrier/outbound`

Everything above is the trunk a call ARRIVES on. Placing one takes a second, different object:
**origination** is where the carrier sends a call that arrives at this box, **termination** is
where the box sends one it places. A Twilio trunk carries both, which is why the provisioning
below looks up the very trunk the import made; a SIP peer's two directions are two addresses, and
the box only ever knows the one the tenant declared.

`GET /v1/carrier/outbound` is whether this org can dial at all:

```json
{ "ready": true, "kind": "twilio", "from_numbers": ["+34910000000"], "steps_missing": [],
  "guards": { "dial_anywhere": false, "per_minute": 6, "per_day": 200,
              "max_duration_s": 600 } }
```

`steps_missing` is one sentence per thing still to do, in the order somebody would do them — no
carrier, a SIP peer with no outbound host, no number imported (a call back is shown as one of the
org's own numbers, so there has to be one), no LiveKit pair on this gateway, no trunk provisioned
yet, or a trunk provisioned for a carrier the org no longer has. `ready` is `steps_missing` being
empty and nothing else. `guards` is the org's standing [dial policy](operator-api.md) read back,
which only an operator sets.

`POST /v1/carrier/outbound` provisions it, each write looked up before it is made and named in the
answer's `steps`, exactly as an import's are; `?dry_run=true` answers the same `steps` with the ids
that stand today and writes nothing. **On Twilio**, four:

1. **the carrier's trunk**: `<fleet>-<org>` on the tenant's account — the one the import already
   made, found by name, created here only when there is none.
2. **the termination label**: the trunk's `domain_name` set to `<fleet>-<org>` in Twilio's alphabet
   (`pinecall-<org>` for the default fleet), so the box dials `<label>.pstn.twilio.com`.
3. **the credential list**: one named `<fleet>-<org>`, its username the same and its password
   minted here, kept sealed under the vault key and never read back — **Twilio shows a
   credential's password exactly once**, which is why the box remembers rather than asks. A box
   behind a changing address would stop dialling the day its IP moved if it authenticated by ACL.
4. **the SFU's outbound trunk**: one LiveKit outbound trunk per org, `<fleet>:<org>:out`, pointed
   at that termination host with those credentials and carrying the org's own numbers as the ones
   it may show.

**On a SIP peer** there is one step and no provisioning: this box creates nothing on somebody
else's switch. It dials `outbound_host` over `outbound_transport`, authenticating with
`outbound_username` and `outbound_password` — or the pair the peer registers with — and then makes
the SFU's outbound trunk as above. The peer's own ACL has to admit this box's public address;
that is the carrier's fence and not this one's.

The answer is `{steps, dry_run: false, ready: true, trunk, address}`, or `{steps, dry_run: true,
ready: false}` for a plan. Refusals: `404` no carrier yet; `409` the org has imported no number, or
a SIP peer that declares no `outbound_host`; `409` also a credential list named `<fleet>-<org>`
standing on the account whose password this box no longer holds — a second list would leave two
logins nobody can tell apart, so it stops and says to delete that one in Twilio's console and run
again; `503` no LiveKit pair on this gateway, or no `PINECALL_VAULT_KEY`; `502` Twilio's own
sentence.

Placing a call through it is `POST /v1/agents/{slug}/dial` — [console-api.md](console-api.md) §4,
where the guards and their refusals are.

## What this does not do

Release a bought number from the box's Twilio account: that is money and a decision, and it is
made in Twilio's console by the operator. Provision anything on a SIP peer: an address, a
credential and an ACL entry on somebody else's switch are theirs to write.
