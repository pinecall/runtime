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

1. **the carrier's trunk** (Twilio only): a trunk named `pinecall-<org>` on the tenant's account,
   created once; its one origination URI set to the box — `sip:<PINECALL_DOMAIN>:5060;transport=udp`,
   the port livekit-sip listens on and the fence opens; the number attached to it. A trunk carrying
   two origination URIs is a person's call and refused: nothing is moved.
2. **the SFU's trunk**: one LiveKit inbound trunk per org, `pinecall-<org>`, with the number added
   to its allow-list; Twilio's signalling networks as `allowed_addresses` (the very set the fence
   opens, held equal by a test), or a SIP peer's own addresses and its username and password; and
   one dispatch rule, one room per caller, the fleet's worker dispatched into it.
3. **the route**: `(org, number) → agent`, on `channel` (`phone` or `whatsapp`), in the key's world
   — the row `routes add` writes, so moving the number later is a route change and no wiring.

`?dry_run=true` answers the same `steps` with the ids that stand today and writes nothing: what a
person reads before letting the gateway touch a carrier account. Refusals: `404` no carrier yet, or
a number the account does not own; `400` a channel with no number, a number that is not E.164;
`503` no `PINECALL_DOMAIN`, or no LiveKit pair on this gateway; `502` Twilio's own sentence.

## Moving one between the worlds — `PUT /v1/numbers/{number}/env {env}`

An org buys ONE number, so a team wanting to try a new agent on the real line has nowhere to try
it: a second number is a second bill. This points the org's own number at the other world and
back. It is one row — `routes.env` — so it takes effect on the next call, and the carrier account
and both trunks are untouched, because a call arrives at this box whichever world answers it.

It is the one numbers door that does not work in the key's world alone: crossing the two is the
point of it, and the number is the org's either way. Answers `{route, moved, from}`; a move to
where the number already is writes nothing and answers `{route, moved: false, said}`. `404` for a
number this org does not have at all, `400` for a word that is neither world.

Whose corner a ring lands in, once a world is answering it, is the caller's phone and then the
**line** — [gateway-api.md](gateway-api.md) §3.

**A number in production still reaches a developer's own phone's sandbox copy.** Moving the number
is for a team trying an agent on the real line for an afternoon; one developer testing needs no
move. A phone call to a production number that no dispatch aimed is asked about first (`GET
/v1/agents/{slug}/rings-for?caller=`, the worker's question): when the phone dialling is one a
developer registered with `pinecall line from` (`PUT /v1/line/from`) and they are holding that
agent in the sandbox, in this org, the call is built in their sandbox corner and its log says
`diverted_from: production`. Every other caller reaches production, and so does that phone the
moment the developer stops holding the agent — or whenever the gateway cannot be asked.

Which numbers those are, a developer's key cannot read off `GET /v1/numbers`: that door answers the
key's own world, to a key that opens `numbers`. `GET /v1/line/numbers` (`app`, a key naming a
person, the sandbox) answers `{calling, numbers}` — the phones this person said are theirs, and
the org's production phone numbers with the agent each reaches — and nothing else about a route.
It is what the local console's Phone testing screen reads (`pinecall serve`).

## Letting one go — `DELETE /v1/numbers/{number}`

The route removed and the number off the org's SFU trunk. The carrier account is not touched: the
number stays on the tenant's trunk, theirs to reattach or move in their own console. `404` for a
number this org never imported in this world.

## Buying one — `POST /v1/numbers/buy {country, area_code?, agent, channel?}`

For an org with no carrier of its own: a number bought on the **box's** Twilio account and billed
to the box, then wired exactly as an import is. The gateway needs `TWILIO_ACCOUNT_SID` and
`TWILIO_API_SECRET` (an API key's secret, with `TWILIO_API_KEY`; or the auth token) — the same
three names `infra/tools/twilio_trunk.py` reads — or the door answers `503` and says to bring a
carrier and import instead. The steps, in the answer's `steps`:

1. **buy**: Twilio's own search for one local, voice-capable number in that ISO country and area
   code (`404` when it has none there); a dry run names the number it found and pays for nothing.
2. **the box's trunk**: the trunk named `pinecall` on the box's account — the one `twilio_trunk.py`
   wires for an operator — created once if the box has none, its origination URI the box, the
   number attached.
3. **the SFU's trunk** and 4. **the route**, as an import: the org's own LiveKit inbound trunk
   `pinecall-<org>` admits the number from Twilio's networks, and the route is written with
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
              "countries": [], "max_duration_s": 600 } }
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

1. **the carrier's trunk**: `pinecall-<org>` on the tenant's account — the one the import already
   made, found by name, created here only when there is none.
2. **the termination label**: the trunk's `domain_name` set to `pinecall-<org>`, so the box dials
   `pinecall-<org>.pstn.twilio.com`.
3. **the credential list**: one named `pinecall-<org>`, its username the same and its password
   minted here, kept sealed under the vault key and never read back — **Twilio shows a
   credential's password exactly once**, which is why the box remembers rather than asks. A box
   behind a changing address would stop dialling the day its IP moved if it authenticated by ACL.
4. **the SFU's outbound trunk**: one LiveKit outbound trunk per org, `pinecall-<org>-out`, pointed
   at that termination host with those credentials and carrying the org's own numbers as the ones
   it may show.

**On a SIP peer** there is one step and no provisioning: this box creates nothing on somebody
else's switch. It dials `outbound_host` over `outbound_transport`, authenticating with
`outbound_username` and `outbound_password` — or the pair the peer registers with — and then makes
the SFU's outbound trunk as above. The peer's own ACL has to admit this box's public address;
that is the carrier's fence and not this one's.

The answer is `{steps, dry_run: false, ready: true, trunk, address}`, or `{steps, dry_run: true,
ready: false}` for a plan. Refusals: `404` no carrier yet; `409` the org has imported no number, or
a SIP peer that declares no `outbound_host`; `409` also a credential list named `pinecall-<org>`
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
