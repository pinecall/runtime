# Numbers — a tenant's own carrier, its numbers imported

Until 2026-09-12 a number reached the box through the one trunk an operator wired by hand with
`infra/tools/twilio_trunk.py`: the box's Twilio, the box's trunk, `routes add` after. A tenant with
numbers of its own — a Twilio account, or a carrier or a PBX that speaks SIP — has the gateway do
that wiring for them, on THEIR account, from the console's Numbers screen. Every door below takes
the org's key with the `numbers` scope.

## The carrier — `PUT /v1/carrier`

```json
{ "kind": "twilio", "account_sid": "AC…", "user": "SK…", "secret": "…" }
{ "kind": "sip", "username": "pbx", "password": "…", "addresses": ["203.0.113.0/24"] }
```

One per org, replaced whole, sealed under `PINECALL_VAULT_KEY` exactly as a provider key is (a
runtime with no vault key answers `503` in the vault's sentence). A Twilio account is opened once
to check — `400 Twilio refused these credentials` otherwise; `user` is an API key SID or the account
SID again with the auth token. `GET /v1/carrier` answers `{kind, account}` and never a secret;
`DELETE` forgets it, and the numbers already imported stay routed until each is let go.

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

## What this does not do

Release a bought number from the box's Twilio account: that is money and a decision, and it is
made in Twilio's console by the operator.
