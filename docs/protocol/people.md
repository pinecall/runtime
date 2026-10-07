# People — how a person gets a key, what a key is, and the org's people

A person has one key per device, minted when they sign in, and it acts as them: the scopes of
their role, the org they signed into, and the world each request names (`pinecall-env`, the
sandbox when unsaid; production only with production access, read from their row on every
request). A server has a token of its own, made by a person for one world. The doors below mint,
list and stop both, and manage the people they belong to. Every refusal is `{"detail": "…"}` with
its status; a key, a password or a link is in exactly one answer, the one that made it.

## Before a key — `GET /.well-known/pinecall`

No key. What a sign-in page reads first:

```json
{"version": "0.1.1", "signup": false, "min_password": 8, "mail": true,
 "brand": {"name": "Pinecall", "logo_url": null, "accent": "#5b3df5"}, "google": false}
```

`signup` is `PINECALL_SIGNUP`; `min_password` is `PINECALL_MIN_PASSWORD` (0 is no rule); `mail`
says whether the box itself can post a letter (an org's own mailbox is not counted), so the page
knows whether "Forgot your password?" can promise one; `brand` is the box's. Box-wide "Continue
with Google" is not in this version: `google` is always `false`, and `GET /v1/login/google` and
its callback answer `503`.

## Who a key is — `GET /v1/whoami`

Any key. The org as an id and as the slug people type, the key's id and label, the world this
request acts in, its scopes, whose it is, and whether it may act in production:

```json
{"org": "org_…", "slug": "clinica-norte", "key_id": "k_…", "label": "ana-laptop",
 "env": "sandbox", "scopes": ["app", "calls", …], "subject": "m_…", "name": "Ana García",
 "email": "ana@clinica.test", "operator": false, "visiting": false, "production": true}
```

`email` is the one name a person carries into every org; a server's token has none. `operator`
says the person runs the box; `visiting` that they are inside an org they are no member of.
Never the key nor its hash. With no key it is `401 this door takes an API key`.

## Signing in — `POST /v1/login`

No key. The body says one of two things, and a body with both or neither is `400`:

```json
{"email": "ana@clinica.test", "password": "…", "org": "clinica-norte", "device": "phone"}
{"code": "lc_…", "device": "chrome"}
```

A password gives a key of that person's in the org named, or, unnamed, the first org the password
opens. A wrong address, a wrong password and a wrong org are one sentence, `401 nobody answers to
that email and password`, and cost the same time. Only once the password matched does the door say
more: `403` a member disabled, `403` an org that signs in with its provider only (naming
`/v1/login/sso?org=…`), `403` somebody invited who has not chosen a password. `device` labels the
key in the org's list, so it is revoked on its own.

Every address is five tries a minute from one place, whatever the password: the sixth is
`429 too many attempts for …: try again in a minute`. The same count holds `POST /v1/login/orgs`,
`POST /v1/login/reset` and `POST /v1/login/sso/discover`. Behind the box's own Caddy the place is
the person's address.

A code gives a copy of the key that minted it: the same org, world, scopes and person, dying when
it dies, labelled `console` unless `device` says. A code is spent once and lives five minutes;
spent, dead or invented it is `404`. Every door that mints a key answers the same shape, the key
this once:

```json
{"key": "pc_live_…", "key_id": "k_…", "org": "org_…", "label": "phone", "env": "production",
 "scopes": [...], "subject": "m_…", "name": "Ana García"}
```

A person's key starts `pc_live_` and opens both worlds.

`POST /v1/login/codes`, with any key, mints `{code, expires_at}`: a browser signs in with it, so a
key never rides a URL. A server's key gives the browser a copy of itself, and revoking a key
revokes every copy made of it, and every copy of those. `POST /v1/login/orgs {email, password}` lists the orgs a password opens,
`{orgs: [{org, slug, name, role}]}`, minting nothing.

## Another org — `GET /v1/login/orgs`, `POST /v1/login/org {org}`

A person's key. The list is every org of the person, oldest first, with `role`, `status`, whether
this key is the one opening it (`here`) and `member: true`. A person who runs the box also sees
every other org, `member: false` and `role: "operator"`. The switch mints the same person's key in
another org of theirs; switching to one where they are still `invited` takes the seat (a proven
address and a password elsewhere; an org that signs in only with its provider seats nobody this
way). A person who runs the box enters any org as a visit, with an admin's scopes and no seat. A
server's token names nobody and is refused both.

## A terminal — `/v1/login/pairings`

`pinecall login` asks `POST /v1/login/pairings {device}` for a word, `{code: "cli_…",
expires_at}`, and opens `/cli?c=<word>` in a browser. The page reads `GET
/v1/login/pairings/{code}` (`{device, expires_at, answered}`, spending nothing) and a signed-in
person approves with `POST /v1/login/pairings/{code}` (`{device, org, org_name}`, the org by its
id and by the name the page says it signed in to): the terminal gets a key of its own, for the
same person, labelled with its `device` and dying with the browser's key. The terminal polls `GET
/v1/login/pairings/{code}/key`: `202 {}` while nobody approved, `{key}` once, `404` after. A word
lives ten minutes; one collected, dead or invented is the same `404`, and a second approval `409`.

## A forgotten password — `POST /v1/login/reset {email}`

No key. Always `202 {}`, for an address known or not. Where the person is active in an org that
signs in with passwords, one letter goes, through the box's own mailbox and never an org's, with
a one-use link to `/invitations/<token>`; the link proves the address, so the password it sets is
the person's one, in every org. A member still invited gets none, since their invitation is the
link. A box with no mailbox mints nothing, so a link an admin handed over is never spent by a
letter that cannot go.

## An invitation — `POST /v1/invitations/{token} {password, device}`

No key: the token is the credential, and lives a week. The password is set for the person in
every org they are in (one password per person), the member is active, and the answer is the key
shape above with `member` beside it. A token spent, dead or invented is one `404`; a password
under `min_password` is `400`.

## The org's people — `/v1/members`

`team`. `GET` lists every member, oldest first, the disabled too:

```json
{"id": "m_…", "email": "ana@clinica.test", "name": "Ana", "role": "developer", "agents": [],
 "status": "active", "scopes": [...], "operator": false, "production": false, "verified": true}
```

`POST {email, name, role, agents, production}` invites, `201 {member, token, expires_at, mailed, link}`,
within the org's `seats` (`429` past them). The invitation is mailed where a mailbox can post it;
`mailed` says it was queued. The token is handed to the admin only when the address is in no other
org: the link sets the person's one password, so for somebody known elsewhere it is mailed only,
and `token` is `null`. Nobody is seated by being named: the member is `invited` until the person
takes the seat, by the link, or, proven elsewhere and with a password, by opening the org
(`POST /v1/login/org`, or a sign-in that names it). A sign-in that names no org never takes an
invitation. An address already a member is `409`.

A key grants only what it holds: a manager invites a `qa` and is refused an `admin` (`403` naming
what the key opens), and production access only by somebody who has it.

`agents` is the agents the member works on; an empty list is every agent of the org, and every
member starts with one. A person's key whose list is not empty is refused, `403` naming the list,
at every door that names an agent outside it: in its path (`/v1/agents/{slug}/…`), as `?agent=`, in
its body (a token, a code, a number routed, an eval run or voiced, a call opened, a callback), in
`WS /v1/chat?agent=` and a socket's `agent.register`, and at a call's own doors (its events, state,
recording, settings, seats, verbs, judging, erasure) by the agent the call was of. A door that
names no agent (`/v1/agents`, `/v1/sessions` without `?agent=`, `/v1/events`) is not narrowed by
it. A server's token holds no list, and a visit to another org is not bound by the list of the
visitor's own.

`PATCH /v1/members/{id} {role?, agents?, status?, production?}` changes what is named. Nobody
changes their own role or production switch, or disables themselves (`409`); an admin always opens
production (`409` taking it away); an invited member becomes active only by accepting (`400`).
Disabling revokes every key of theirs at once; the rows stay, since the log names who did what.
`DELETE /v1/members/{id}` takes a member out for good, keys revoked, seat freed, `204`; never
yourself and never the org's last active admin (`409`). `POST /v1/members/{id}/reset` is an
admin's one-use link for an active member, handed and mailed as an invitation is; `409` for
anybody not active.

## The org's keys — `/v1/keys`

`GET` with any key lists by fingerprint every server's token and the asker's own person keys;
a key with `keys` sees every person's too. Never a key:

```json
{"fingerprint": "…", "label": "clinica web", "kind": "server", "env": "production",
 "name": null, "created_by": "Ana", "created_at": "…", "last_used_at": null,
 "revoked_at": null, "scopes": ["app", "calls", "evals", "knowledge", "talk"]}
```

`POST {label, env, scopes?, expires_at?}` with a person's key that opens `app` makes a server's
token for one world, `pc_live_` or `pc_test_`, answered once; a production token only by somebody
with production access. It opens `app`, `calls`, `talk`, `knowledge` and `evals`, or, with
`scopes`, only the ones named of those (`400` for any other, or none). With `expires_at`, a moment
to come with its offset, it opens nothing from then on and every door answers `401 this key
expired at …`; without it, it never expires. The listing carries each key's `expires_at`. `POST /v1/keys/{fingerprint}/revoke`
stops your own key, a token you made, or any with `keys`; anything else, revoked or not the org's
is one `404`. The row stays.

## Sign-up — `/v1/signup`

Shut unless the operator sets `PINECALL_SIGNUP` (`404` naming it). With `PINECALL_SIGNUP_KEY` set,
the three doors take that bearer key alone, and the address they count is the
`X-Pinecall-Client` the page in front of them sends; without it that header is never believed.

`POST {org, name, email, person, password, device}` keeps the sign-up and mails six digits:
`202 {email, code_expires_at}`, never the code, and no org yet. A box that cannot mail takes
none (`503`). Every address is answered alike, so the door says nobody whether one has an
account: an address with a password signs up with it, and with another is mailed that it has an
account instead of a code; one invited somewhere is mailed to accept that first. A slug taken is
`409`. `POST /v1/signup/verify {email, code}` makes
the org, allowed what the box's admission gives a newborn org, its admin active, and answers
`201` with the key shape plus `slug`, `member`, and a login `code` for the console. A code lives
15 minutes and six tries; a wrong, burned or expired code is `400` in its words, and an address
nobody signed up with reads as a wrong code. `POST /v1/signup/resend {email}` mails a new code
and kills the first; `202 {}` whoever asks.

## The org's provider — `/v1/org/sso`, `/v1/login/sso`

`team`. `PUT /v1/org/sso {issuer, client_id, client_secret, domains, role?, required}` keeps the
org's OpenID provider after its issuer answered its discovery (an issuer that is not https never
reaches the network, nor one whose name resolves to nothing or to any address that is not
public, before each request; one that does not answer is `400`, nothing kept). The secret goes in and
never comes out; `role` seats an uninvited address of the domains with that role, and counts as
granted by the key. `GET` answers `{configured, issuer, client_id, domains, role, required,
redirect_uri, proofs}`, the URI to register at the provider. `DELETE` lets passwords in again.

A domain is the org's word until it proves it: each one in `proofs` is `{domain, txt, verified}`,
and the org publishes `txt` (`pinecall-verify=<token>`) as a TXT record at the domain, then asks
`POST /v1/org/sso/domains/{domain}/verify`, which reads the record now and answers the proof
verified (`400` saying what is at the domain instead, `404` for a domain the SSO does not name).
Until a domain is verified, discovery offers the provider to nobody of it and the callback seats
nobody of it: an org naming a domain that is not its own sends none of that domain's people to
its provider. A record once seen stands. `required: true` is `409` until one domain is verified,
or nobody could sign in. The domains declared before this proof existed read verified.

`GET /v1/login/sso?org=&pairing=` is `302` to the provider with a state, a nonce and a PKCE
challenge; an org unknown or without a provider is the same `404`. The callback exchanges the
code, checks the id_token (the issuer, the audience, the nonce, a verified address of the org's
domains), seats the person and is `302` to `/?login=<code>`, or `/cli?c=<word>&login=<code>` when
a terminal waits; any refusal there is `302` to `/?refused=<why>`, and only a state nobody began
is `400`. No key is ever in a URL. `POST /v1/login/sso/discover {email}` names the orgs whose
provider signs in that address's domain, among the domains they proved, and nothing about who
exists.

## The org's mailbox — `/v1/org/mail`

`team`. `PUT {host, port, security, username, password, from}` keeps the SMTP account the org's
letters go out through before the box's: a server on the internet, reached over TLS (`security`
is `starttls` or `tls`; `none` is `400`, the box's own relay alone). Every address its name
resolves to must be public: one inside the box's network, or a name that resolves to nothing, is
never reached, and the letter's error says so. It sends nothing. `GET` answers `{configured, host, port, security, username, from, verified_at,
last_error}`, never the password, and `DELETE` goes back to the box's. `POST /v1/org/mail/test
{to}` sends one letter and waits: `{sent, error}` with what the server said, `409` when nothing
can send.

## The box — `GET /v1/ops/whoami`

The operator's key, or a person the box made an operator: `{operator: true, version, domain,
name, org}`, `name` and `org` null for the box's own key. The console opens its Box screens when
this answers `200`.
