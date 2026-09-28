# Numbers — the accounts an org's numbers live in, and how a number reaches the box

A number reaches an agent in three writes: the carrier points it at the box, the box admits it,
and a route says which agent answers. The doors below do all three. Every one takes the org's
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
is `400 Twilio refused these credentials`. A peer's `addresses` are the networks it calls from;
its four `outbound_*` fields say where the box dials it, and unsaid the box dials with the pair it
registers with. `outbound_username` without `outbound_password` is refused.

`GET /v1/carrier` answers `{kind, account, label}` for the org's only account, or the one
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
  carrier or PBX: `sip:+59829000000@<PINECALL_DOMAIN>:5060`. Nothing outside is touched; the box
  admits the number from `networks` (Twilio's signalling networks when unsaid) and routes it.

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
| more dials this minute than `per_minute` (6), refusals counted | `429` | a wait |
| more dials today than `per_day` (200) | `429` | a wait |

The count and the row are one transaction under the org's lock: two dials at once cannot both take
the last slot. A placed call runs at most `max_duration_s` (600), which the media plane enforces.
A far end that is busy, declines or never answers ends the call `busy` or `no_answer`; anything
else, `dial_failed`.

A leg dialled into a live call (a warm transfer, `room.invite`) asks
`GET /v1/agents/{slug}/outbound-trunk?to=&call=&from=`, the worker's: the shape and the pace, not
the stranger fence, and the trunk inline in the answer.

## The firewall

livekit-sip listens on 5060, and the box's nftables opens it to Twilio's signalling networks alone.
A SIP peer that calls the box from networks of its own needs the operator to add them to
`carrier_signalling` in `infra/box/nftables.conf`.

## Reconcile at start

LiveKit keeps its trunks and rules in Redis, which can be emptied; the tables are the truth. At
start the gateway admits every routed phone number again with its fence and its world's rule, one
org's refusal logged and the others going on. It deletes nothing and never touches the carrier.
