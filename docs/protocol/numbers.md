# Numbers — the accounts an org's numbers live in, and how a number reaches the box

A number reaches an agent in three writes: the carrier points it at the box, the box admits it,
and a route says which agent answers ([telephony.md](../telephony.md) is how the three fit). The doors below do all three. Every one takes the org's
key with the `numbers` scope and acts in the key's world: a `pc_live_` key imports into
production, a `pc_test_` key into the sandbox, a person's key into the world the request names.

## The accounts — `PUT` · `GET` · `DELETE /v1/carrier`, `GET /v1/carriers`

An org holds as many accounts as it has: several Twilio accounts, a SIP peer (its own PBX or a
carrier with no API here), a WhatsApp number at Meta. Each is one row, sealed under
`PINECALL_VAULT_KEY`, named by its own id and never by its secret.

```json
{ "kind": "twilio", "account_sid": "AC…", "user": "SK…", "secret": "…", "label": "Clínica" }
{ "kind": "sip", "username": "pbx", "password": "…", "addresses": ["203.0.113.0/24"],
  "outbound_host": "sip.carrier.example", "outbound_transport": "tls" }
{ "kind": "whatsapp", "phone_number_id": "1055…", "access_token": "…" }
```

`PUT /v1/carrier` adds an account, or replaces the secret of one the org already holds. A Twilio
account is opened once first: `user` is an API key SID (make one at Twilio → Account → API keys,
revoke it there any time) or the account SID again with the auth token, and a pair Twilio refuses
is `400 Twilio refused these credentials`. A peer's `addresses` are the networks it calls from:
each is an IPv4 address or a network no wider than a `/24`, public (a private, shared, loopback or
documentation range is `400`), and each waits for the box's operator to approve it before 5060
opens to it or a trunk lists it ([operator-api.md](operator-api.md), "Carriers and the fence").
Its four `outbound_*` fields say where the box dials it, and unsaid the box dials with the pair it
registers with. `outbound_username` without `outbound_password` is refused.

`GET /v1/carrier` answers `{kind, account, label, networks}` (a peer's networks, each
`{network, state}`: `waiting`, `approved` or `refused`) for the org's only account, or the one
`?account=` names; with several and none named it is `409` naming them. `GET /v1/carriers` is every
one of them. `DELETE /v1/carrier` forgets an account; its numbers stay routed until each is let go.

An agent answers at as many numbers as the org routes to it: phone numbers from any of its
accounts, hooked by the org or bought by the box, and WhatsApp numbers, each one row. A call the
agent places is shown as one of them, and dials through the account that number lives in.

## What the accounts own — `GET /v1/numbers/available`

`{kind, numbers: [{number, name, imported, account}]}`: every number every Twilio account of the
org owns, all of Twilio's pages, and whether this world imported it. A SIP peer owns what it owns
and nobody here can list it: `{kind: "sip", numbers: []}`, and its import takes the number typed.

## Importing one — `POST /v1/numbers {number, agent, channel?, account?, hooked?, networks?, move?}`

Two ways to hook a number, both through this door:

- **We hook it** (the default): the number is one of an account of the org. On Twilio the box
  finds the account's trunk that points at it (by its origination URI,
  `sip:<PINECALL_DOMAIN>:5060;transport=udp`, whatever the trunk is named), makes one if there is
  none, and attaches the number. On a SIP peer nothing outside is touched. With several accounts,
  `account` says which.
- **You hook it** (`hooked: true`): the org points the number at the box itself, from any
  carrier or PBX: `sip:+59829000000@<PINECALL_DOMAIN>:5060`. Nothing outside is touched. The box
  admits the number from the networks of the carrier named in `via`, one of
  `GET /v1/carriers/catalog` (Twilio's when unsaid), or from `networks` of the org's own, which
  wait for the operator's approval like a peer's: until then the number is routed and on no
  trunk, and the import's steps say so.

`GET /v1/carriers/catalog` is `{carriers: [{kind, name, how, networks}], sells}`: the carriers this
box's operator admits, `automatic` for one the box drives through its API (Twilio), `guided` for one
whose portal the org types the address above into; `sells` says whether `POST /v1/numbers/buy`
has an account to buy on.

```
$ curl -X POST https://box.pinecall.io/v1/numbers -H "authorization: Bearer $PINECALL_KEY" \
    -d '{"number": "+59829000000", "agent": "recepcion"}'
{"route": {"org": "org_…", "agent": "recepcion", "channel": "phone", "number": "+59829000000",
           "label": null, "env": "production", "managed": false},
 "steps": ["Twilio: a trunk sending calls to sip:box.pinecall.io:5060;transport=udp: stands",
           "Twilio: +59829000000 attached to it: done",
           "LiveKit: trunk org_… admits +59829000000: done",
           "LiveKit: rule org_…:production sends it to fleet pinecall: done",
           "route: +59829000000 to recepcion in the production: done"],
 "dry_run": false}
```

Each step is looked up before it is written, so a second run of an interrupted import writes
nothing twice: every step says `stands`. `?dry_run=true` answers the same steps with `to do` and
writes nothing anywhere. On the SFU an org has one inbound trunk per fence (`<org>` for Twilio's
networks, `<org>:<peer>` for a SIP peer, `<org>:<number>` for a number hooked from networks of
its own) and one dispatch rule per world (`<org>:production`, `<org>:sandbox`) whose numbers are
that world's, dispatched to that world's fleet. A number changes world by moving between the two
rules; its trunk never moves. A trunk or a rule left listing no number is deleted, because one
that lists none takes every number.

Refusals: `404` no account, or a number the account does not own; `400` a channel with no number
or a number that is not E.164; `409` several accounts and none named, a number another org's trunk
on this box already lists (livekit-sip refuses an INVITE two trunks list, so nothing is written),
or a number attached to another trunk of the same Twilio account — its name and where it points
are in the sentence, and `move: true` takes it off there first; `503` no `PINECALL_DOMAIN`; `502`
Twilio's own sentence.

## Buying one — `POST /v1/numbers/buy {country, area_code?, agent, channel?}`

For an org with no account of its own: a number bought on the box's own Twilio account (the
sealed `credentials/twilio` row of `box_settings`), then hooked as an import and routed with
`managed: true`, in the key's world. The `numbers` quota of that world caps it, and is counted on
the managed numbers alone: `429` before Twilio is asked. `404` when Twilio has nothing for sale
there, `503` when the box holds no Twilio account. `?dry_run=true` names the number it would buy
and buys nothing.

## The org's numbers — `GET /v1/numbers`, `GET /v1/numbers/{number}/path`

One row per number in the key's world, oldest first: `{route, origin, rings, last_call_at, via,
account}`.
`route` is the number, its channel, the agent and the world; `origin` is how the row was written —
`bought` (by the box), `imported` (from one of the org's accounts), `hooked` (the org pointed the
number at the box itself) or `typed` (by the box's operator, who can route a number into any org).
A row the org did not write is how it learns the operator did; importing the number again makes it
the org's. `via` is the catalog carrier a hooked number comes through, `account` the org's account it
lives in. `last_call_at` is when a
call to the number last reached the box (kept at most once a minute), null when none ever did.

`rings` is what a call to it does now: `ok`, `waiting` (on its first call, or on the operator's
approval of a network) or `broken` (nobody runs its agent, another org's older row answers it),
read off the tables without asking any carrier. `GET /v1/numbers/{number}/path` says why: four
steps, each `{step, state, says, fix}` — the carrier (a Twilio number asks Twilio whether it is
still attached to the trunk pointed here), the fence, the world's rule, the agent — with the worst
of them as `rings` and `last_call_at`; `404` for a number the org has not in the key's world.

## Letting one go, moving one — `DELETE /v1/numbers/{number}`, `PUT /v1/numbers/{number}/env`

`DELETE` removes the route and takes the number off its trunk and its world's rule; the account
keeps it, so nobody is un-bought by a typo. `PUT …/env {env}` moves a number into the other world:
its row and the two rules, nothing else. It is how a number tested in the sandbox goes live.

## Dialling out — `GET` · `POST /v1/carrier/outbound`, `POST /v1/agents/{slug}/dial`

The SFU keeps no outbound trunk: every leg is dialled with its trunk inline. What an account
needs is the other direction of its carrier. `POST /v1/carrier/outbound` (`?account=`,
`?dry_run=true`) makes it once: on Twilio the trunk's termination host
(`<box>-<account>.pstn.twilio.com`, one per account and box), a credential list on that trunk,
and a credential per org on the list, its password minted here and kept sealed because Twilio
shows it once. Two orgs that brought the same account dial through one trunk, each with its own
credential. A credential this box made and no longer holds the password of is `409` naming it: it
is deleted in Twilio's console, then provisioned again. A SIP peer needs nothing made: it is
dialled at its `outbound_host`.

`GET /v1/carrier/outbound` is `{ready, kind, from_numbers, steps_missing, guards}`: whether the org
can place a call, one sentence per thing still missing, and the guards it dials under.

`POST /v1/agents/{slug}/dial {to, from?, log?}` places a call as the agent: `202 {call, agent, to,
from, env, log_token}` before anything rings. `from` is one of the agent's own numbers in the key's
world, the first unsaid; the call is dialled through the account that number lives in, and a
number the org hooked itself or the box bought dials through none (`409`). Nobody holding the
agent is `409`: a phone would ring with no app to serve it. Every dial asked for is one row of the
org's `dials` ledger, taken or refused:

| refused when | status | lifted by |
|---|---|---|
| `to` is not E.164, has no country calling code, is a satellite or global-service range (`+870`, `+878`, `+881`, `+882`, `+883`, `+888`, `+979`), or has fewer than five national digits | `400` | a number somebody could answer |
| the number never called or wrote to this org **in this world** | `403` | an operator's `dial_anywhere` |
| the number is on the org's **do-not-call list** in this world (`do_not_call`): its newest fact is an opt-out — the caller asked the agent (`call.opt_out`), a person put it there, or the org imported it. A consent sent with the dial never lifts it | `403` | a consent recorded at `POST /v1/org/consents` |
| no **consent** on file for a `+1` number (`no_consent`), nor one sent with the dial as `consent: {kind: express\|written, source, text?, evidence?}`; any country when the org's policy says `consent_everywhere` | `403` | the consent, recorded or sent |
| outside the called number's hours in **every** zone it could be in (`quiet_hours`): for a `+1` number 8:00 to 21:00 local (the Telemarketing Sales Rule), narrowed by the org's `calling_hours` and never widened, and a `+1` number with no zone (toll-free) always; elsewhere the org's `calling_hours`, when it set some | `403` | the hour |
| more dials this minute than `per_minute` (6), refusals counted | `429` | a wait |
| more dials today than `per_day` (200) | `429` | a wait |
| the number itself rung `per_number_day` times in 24 hours (`too_often`): 3 for a `+1` number unless the org sets its own, none elsewhere unless it does | `429` | a wait |

The list, consent, the hours and the per-number count bind a call to somebody: the **sandbox** is
held to none of them, and neither is a number the person who asked verified as **their own phone**
(`PUT /v1/line/from`) — that is them testing. A consent sent with a dial is written down with the
call's id before anything rings, so the next call to the number needs none. The zones come from libphonenumber (`phonenumbers`); the org sets its hours
and count at `PUT /v1/org/policy` ([gateway-api.md](gateway-api.md) §7). The count and the row are one transaction under the org's lock: two dials at once cannot both take
the last slot. A placed call runs at most `max_duration_s` (600), which the media plane enforces.
A far end that is busy, declines or never answers ends the call `busy` or `no_answer`; anything
else, `dial_failed`.

A leg dialled into a live call (a warm transfer, `room.invite`) asks
`GET /v1/agents/{slug}/outbound-trunk?to=&call=&from=`, the worker's: the shape and the pace, not
the stranger fence, and the trunk inline in the answer.

## The firewall

livekit-sip listens on 5060, and the box opens it to the signalling networks of the carriers its
operator admits and to the addresses he approved, and to nothing else: Twilio's are typed into
`infra/box/nftables.conf`, the rest are written every minute by `pinecall-fence apply`
(as root, `pinecall-fence.timer`) from the catalog and the approvals. On a GCP box the cloud's own
firewall stands in front and is Terraform's ([operator-api.md](operator-api.md)).

## Reconcile at start

LiveKit keeps its trunks and rules in Redis, which can be emptied; the tables are the truth. At
start the gateway admits every routed phone number again with its fence and its world's rule, one
org's refusal logged and the others going on. It never touches the carrier, and it takes a number
off its org's trunks when nothing approved fences it any more. The operator's approval or refusal
of a network does the same for that org at once.


## Consent and the do-not-call list

Every fact about a number is a row, never changed: a consent given (`express` or `written`, where it
came from, the words the person agreed to, a proof) or an opt-out, by whom and on which call. What
stands is the newest row, so a person who opted out and later consented is called again, and the
history of both is kept. An erasure of the contact leaves these rows: an opt-out has to outlive the
person's data, or the next list the org imports calls them again.

| door | scope | what |
|---|---|---|
| `POST /v1/org/consents {number, kind, source, text?, evidence?}` | talk | one fact written; what stands for the number after |
| `GET /v1/org/consents/{number}` | calls | `{number, standing: consented\|opted_out\|unknown, rows}`, newest first |
| `DELETE /v1/org/consents/{number}` | talk | the number put on the list: an opt-out written, the history kept |
| `GET /v1/org/dnc?after=` | calls | the numbers whose newest fact is an opt-out, newest first, `next` for the page after |
| `POST /v1/org/dnc {numbers, source}` | talk | the org's own list, or its scrub of the National Do Not Call Registry, onto the list at once: `{added, refused}` |

The runtime does not query the National Registry: its subscription is the seller's by law. The org
scrubs and imports what it found.
