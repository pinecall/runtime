# People — members, login, and the sign-up a gateway may open

Section 8 of [gateway-api.md](gateway-api.md), on its own page: the org's people as rows, the
key a person logs in for, the code a browser spends, and the one door a stranger may knock at
where its gateway opens one.

An org's people are rows, not shared keys. A key holder invites one — `POST /v1/members` with
`{email, name, role, agents?, production?}` answers `201` with the member, a one-use `token`, shown once and
dead in a week — in the answer only for an address that is **this org's alone**; for somebody who is
also another org's here it is `null` and the link is posted to them, because a link buys the
person's ONE password (below) — and `mailed` (below: whether the link was also posted to them) — and the person accepts at `POST /v1/invitations/{token}` with `{password,
device?}` (no key at that door; `min_password` characters at least — `PINECALL_MIN_PASSWORD`, 8 unless the operator set it, below — argon2id at rest) — which is what the
console's own card at `/invitations/{token}` does when the link is opened in a browser — and that
makes them `active` and answers their **first key**, in the one shape a key travels in: `{key, key_id, org,
label, env, scopes, subject, name, member}`. `subject` is the member's id and `scopes` the preset of
their role: `qa` · `supervisor` · `manager` · `admin` · `developer` (`types/member.py`). `GET
/v1/members` lists them, each saying `production`; `PATCH /v1/members/{id}` replaces `role`,
`agents`, `status` or `production` — `disabled` revokes every key of theirs and refuses their
login, `active` re-enables one who had a password and never activates one still invited.

**A key grants what it holds** (`auth/grants.py`), at the invitation, at the PATCH and at the
role `PUT /v1/org/sso` seats a stranger with. A role whose preset opens a door the asking key does
not is `403 this key does not open everything <role> would: it opens …, so it cannot grant <role>`
— a manager seats `qa`, `supervisor` and `manager`, never a `developer` or an `admin`.
`production: true` from a key with no production access is `403 <name> has no production access,
and cannot give it: an admin does`. Your own row is not yours to raise: `role` or `production` on
it is `409 you cannot change your own role or production access: another admin of this org does`
(`agents` still is). A key that names nobody — a server's token, the box's own, an operator's
visit — grants what it is asked to.

**Removing is for good, where disabling is for now.** `DELETE /v1/members/{id}` (`team`) answers
`204`: every key of theirs is revoked FIRST, so there is no moment a removed person's key opens a
door; then the row goes, its open invitation and reset links with it, and the seat is free — the
same address can be invited again and is a new row with a new id. What the log wrote about them
stays readable: a key's `subject`, a dial's `asked_by` and every log entry name the id as text,
and the id simply names nobody now. Two removals are refused `409`, in a sentence: **yourself**
(`you cannot remove yourself: another admin of this org removes you`) and **the last active admin**
(`<email> is the last active admin of this org: make somebody else an admin first, …`) — an admin
still invited does not count, because an org whose only admin has no password is an org nobody
can sign in to. `404 no member <id> in this org` for an id that is not this org's. The operator's
twin is `DELETE /v1/ops/orgs/{org}/members/{id}`, under the same rules less "yourself", and
`pinecall-runtime orgs remove-member <org> <email>` is that door from a terminal.

**A person is their email, on this box, and has one password.** An address is kept and compared
trimmed and lower-cased — `JP@Cloudacio.com ` and `jp@cloudacio.com` are one login — and an org is
a row of theirs: `members` holds one per (org, email), and a second org is a second row carrying the
same hash (`auth/members.py`). So inviting an email that already has a password anywhere on this
box **and is verified** does not send a link: the row is made **`active`** at once, with the
password they have, and `201` answers the member with `token` and `expires_at` null — they sign in
as they always do, and the new org appears in their org switch. An email that has accepted nowhere,
or is not verified, is invited as above, and an email already accepted in THIS org is `409`.
Accepting an invitation sets the password on every row of that email that has one, so a password
chosen in one org is the password in all of them; a row still invited when the person already has
a password is seated at their first login to it — once verified, else `403 … has not accepted
their invitation yet`.

**An address is verified** (`members.verified_at`, `0048`; `verified` on every member the doors
answer) once somebody other than an admin proved it: they accepted a link that travelled by mail
alone (an invitation's or a reset's whose `token` was not in the answer), an identity provider
named them (the org's own, or Google box-wide), or the box's operator invited them
(`POST /v1/ops/orgs/{org}/members`). A link handed to an admin in the answer proves nothing about
who opens it, and a sign-up proves nothing either.

An invitation takes a **seat**, and where the org's plan caps them the door answers `429` with the
quota's own sentence and makes no row. A seat is held by everybody the org has not disabled —
invited counts, or an org at its limit could invite forever and seat them all the moment they
accepted — so disabling somebody is what frees one, and their row stays because the log names
them. Re-inviting an email the org already holds takes no second seat.

**A person holds one key per device, and the request says the world it believes it is in.** The
role says what they do; the member's **`production`** switch, set by an admin, says whether they may
do it in production. Every key minted for a person — at login, at `POST /v1/invitations/{token}`,
at sign-up, at a code, at a terminal's pairing, at another org of theirs — carries their role's
preset whole, `app` included, and opens no world of its own (`auth/person_keys.py`): it is read in the
world of the instance it knocks at (`PINECALL_WORLD`). `pinecall-env: production` or `sandbox` is
an **assertion**: naming the other instance's world is `403 this gateway is <here>'s, not
<asked>'s: <asked> answers at <elsewhere>`. The doors that open no scope — whoami, a login code,
pairing, the org list and switch, one's own keys — read the key as an identity and ask nothing
more, so a person kept out of production still signs in there and is handed to the sandbox. Every
door that opens a scope reads it as it acts: at production a person's request with no header is
`403 this is production, and a person's key says the world it means …` (an old CLI meant the
sandbox by saying nothing), and production answers only while the member's row opens it, read at
every request, so switching it off closes the very next one: `403 <name> has no production
access: an admin gives it in Team`. **An admin always opens production**
— `PATCH` with `production: false` on one is `409 <email> is an admin, and an admin always opens
production`; `PATCH` disabling YOURSELF is `409 you cannot disable yourself: another admin of this
org disables you`. So a developer with the switch holds an agent in production from their own terminal
(`pinecall start --prod`); what normally holds it is a **server's token**, made from the console's
Tokens screen (`POST /v1/keys`, [gateway-api.md](gateway-api.md) §8), which names nobody and
outlives whoever made it. Two people's sandbox keys are two people's: the registry holds a sandbox
slug per person. **A sandbox's person keys live a day**, one minted from another key (a code, the
org switch) never outliving it, and production's never expire; an expired key is `401`, as revoked.

**How short a password may be is the OPERATOR's.** `PINECALL_MIN_PASSWORD` (default 8, `0` for
none) is the floor every door that takes a new password is held to, and `GET /.well-known/pinecall`
carries it as `min_password`, so a card says the rule this gateway enforces rather than a copy.

`POST /v1/login` takes `{org?, email, password, device?}` and answers a key for that person
and that device. The password is checked as the person's, whichever org it was chosen in; then
the row is the one in the org named, or — **with no org** — the oldest row of theirs that is not
disabled, so a person with one org never types it. Every wrong thing — the org, the email, the
password, an email that has accepted nowhere — is one `401` sentence (`no member of <org> answers
to that email and password`, or `nobody answers to that email and password` when no org was named);
a disabled member is `403`; the sixth try in a minute for one name is `429` whatever the password.
Or it takes `{code, device?}`: a key holder minted the code at `POST /v1/login/codes` (five minutes,
one use), which is how `pinecall start` prints `?login=<code>` and a browser ends up holding a key of
its own, never the org's.

**Switching orgs** is two doors on a person's key, no scope asked: `GET /v1/login/orgs` answers
`{orgs: [{org, slug, name, role, status, here}]}`, every org the person belongs to, oldest first,
disabled rows left out and `here` marking the one this key opens; `POST /v1/login/org {org}`, an id
or a slug, answers a key for the same person in that org — the same label as the key that asked, with
what their role there opens, and production as their row there says — or `403 you are not an
active member of <org>`. A server's token names nobody and opens one org: both doors refuse it `403`.

**An operator of the box is shown every org.** Each row of `GET /v1/login/orgs` also says
`member`: `true` for the person's own orgs, which come first, oldest first, exactly as before. For
somebody the box made an operator (`PUT /v1/ops/orgs/{org}/members/{id}/operator`) the rest of
the box follows, oldest first, with `member: false`, `role: "operator"` and `status: "active"`.
`POST /v1/login/org {org}` lets them into any of those: a member there gets the member's key as
above; an operator who is none gets a **visitor's key** —

```json
{"key": "pc_…", "key_id": "k_…", "org": "org_4ad9…", "env": "production",
 "label": "operator · bernardo@pinecall.io", "scopes": ["calls", "evals", "…", "team", "usage"],
 "subject": "operator:bernardo@pinecall.io", "name": "Bernardo"}
```

— `production`, fixed, whatever world the asking request named, the `admin` role's scopes less
`app` — the box mends a tenant and holds none of its agents — and **no member row**: no seat is taken and the org's Team screen gains nobody.
`subject` is `operator:<their address>` and not a member id, so the org's Tokens screen says whose
key it is (and may revoke it), and everything that writes a subject down — a dial's `asked_by`, a
supervise verb, a seat — attributes what they did to a person by address, in the tenant's own
log. A row of theirs the tenant **disabled** is not the way in: they walk in as the operator,
said in so many words, never as the member the org stopped. `GET /v1/whoami` says `operator:
true` for such a person in every org and `visiting: true` inside one they are no member of, with
`name` still theirs; both switch doors work from inside, which is how they get home. A visitor's
key opens no sandbox and signs no terminal in — `pinecall-env: sandbox` is `403 this token was
made for production: …` and the pairing answers `403 an operator visits an org in production,
from the console: …` — because a sandbox is a member's corner. **It stops the moment they stop running the box**: the flag is read on every verify
(`auth/visitor_keys.py`), so `orgs operator --revoke`, disabling them or removing them is one write
and the next request with that key is `401`, with nothing to remember to revoke.

**Before signing in**, a sign-in page asks `POST /v1/login/orgs {email, password}` — no key, and it
mints none — which orgs those open: `{orgs: [{org, slug, name, role}]}`, oldest first, disabled rows
left out. It shares `/v1/login`'s throttle and its one sentence: a wrong password and an email
nobody has are the same `401 nobody answers to that email and password`, and the sixth try in a
minute is `429`.

**A forgotten password is handed back by a one-use link**, which an admin issues or the person
asks for. `POST /v1/members/{id}/reset` (`team`) answers `201 {member, token, expires_at, mailed}`:
a link like an invitation, shown once, dead in a week, that spends every older link of that
member — and, like an invitation's, in the answer only for a person who is this org's alone: one
who is also another org's has it posted to them and `token` null here, because it sets their one
password everywhere. The person opens it and chooses a password at the very door an invitation is accepted at,
`POST /v1/invitations/{token} {password}`, and the password is theirs in every org, as always.
Only an **active** member is reset — `409` for one still invited (their invitation is the link) or
disabled — and a link issued before somebody was disabled opens nothing. Where the box can send
mail, the person asks for their own at `POST /v1/login/reset` (below); where it cannot, the
console's "Forgot your password?" says: ask an admin of your org.

## Two instances: production says who a person is

A sandbox instance keeps no password and makes no person: **every door a person is made, proved,
changed or handed a key by a password at is production's** — on a sandbox the password login and
org picker, pairing, sign-up, SSO, Google, the forgotten password and every member door but `GET
/v1/members` are `404 this is the sandbox, and people sign in at <PINECALL_IDENTITY_URL>: …`. A
code minted at production crosses instead: the sandbox's `POST /v1/login {code}` spends one of its
own, else `POST /v1/login/redeem {code}` at production — keyless, unthrottled (24 random bytes,
spent once) — answers `{org: {id, slug, name}, member: {id, email, name, role, agents, status}}`,
the row as it stands, `disabled` included, never the production switch (`403` for a server's or a
visit's code, or a member removed). The sandbox mirrors both **by production's ids** (no password,
verified; a stale row of the address loses its keys and goes) and mints a key that lives a day —
or, for a member production disabled, revokes every key of theirs there and is `403`. `409` an org
of its own holding the slug; production's refusals pass through; `502` production silent.

## Mail: the letters a box sends

**Three letters, and only where somebody said where to post them.** An invitation (`POST
/v1/members`, and the operator's `POST /v1/ops/orgs/{org}/members`), an admin's reset (`POST
/v1/members/{id}/reset`) and a forgotten password asked for by the person who forgot it. Each is
plain text and the same words as simple HTML, in English, naming the org and — for the first two
— the person who sent it, and carrying the one link: `https://<gateway>/invitations/<token>`, the
console's own card, built from the box's `PINECALL_DOMAIN` exactly as the identity provider's
redirect URI is (the request's own host on a laptop with none). A person already seated at once
— an email that has a password somewhere on this box — has no link to be sent and gets no letter.

**Where they go out.** The transport is generic SMTP, so Amazon SES, Postmark, Mailgun or a mail
server of one's own: `PINECALL_SMTP_URL` (`smtp://user:pass@host:587` for STARTTLS,
`smtps://user:pass@host:465` for implicit TLS, the password percent-encoded) and
`PINECALL_MAIL_FROM` (`Pinecall <no-reply@example.com>`) are **the box's**, a systemd credential
like every other secret. An org may wire **its own** account, which wins over the box's for its
letters; with neither, nothing is sent and every door answers exactly as it did before mail existed.

**`mailed` says a letter was handed over, never that it arrived.** The row is written and the door
answers; the letter leaves in the background, with a ten-second limit, after the answer is gone.
What came of it is written where somebody can read it: on the org's own mail row when the letter
went through it (below), in the box's log when it went through the box's. The token stays in the
answer as it always was, so a letter that never arrives costs an admin nothing they did not have.

`GET /v1/org/mail` (`team`) answers `{configured, host, port, security, username, from,
verified_at, last_error}` — one shape either way, the empty value of each when nothing is wired —
and **never the password**. `verified_at` is the last time that server took a letter; `last_error`
is the sentence it refused the last one with, host and port included and credentials never. `PUT
/v1/org/mail {host, port, security?, username?, password?, from}` (`security` one of `starttls` —
the default — `tls`, `none`) replaces the whole account, resets the standing, and sends nothing:
a door is never blocked on somebody else's relay. `POST /v1/org/mail/test {to}` is the one door
that waits for a mail server, because a person is watching it: `200 {sent, error}`, recorded on the
row exactly as a real letter is, and `409` when neither the org nor the box has a server at all.
`DELETE /v1/org/mail` is `204`, and `404` for an org that wired none. The password is a Fernet
token under `PINECALL_VAULT_KEY`, as a provider key is; a runtime with no vault key answers these
doors `503` in the vault's own sentence — and still posts the box's own letters, which need none.

**A forgotten password, asked for.** `POST /v1/login/reset {email}` — no key — answers **`202 {}`
whoever asks**: not whether anybody answers to the address, not whether their org can send mail,
not whether it signs in with a provider. It shares `/v1/login`'s throttle (`429` on the sixth try
in a minute for one name). Behind the one answer: the oldest org of that person's where they are
`active`, whose org has not set SSO `required`, and whose letters have somewhere to go, is issued
the same one-use link an admin's reset is, and it is posted to them — one letter, never one per org.
A token is minted **only** where a letter will carry it, because a reset spends every older link
of that member: a door that minted one for nothing would let anybody who knows an address kill the
link an admin handed over an hour ago. What the person opens is the invitation card, as above.

## The sign-up, where its gateway opens one

**Only where `PINECALL_SIGNUP` is set**, and it is **off unless the person who runs the gateway
turns it on** — a box somebody runs for their own agents wants no stranger making an org, and is
never asked to close a door. It is its own flag and not `cloud`: a box of its own may want sign-ups,
and a cloud may close them. `GET /.well-known/pinecall` answers `{version, world, elsewhere, cloud,
signup, min_password, mail, brand, google}` with no key — `world` the instance's, `elsewhere` the
other's URL or null — which is how a page or a CLI knows which world a URL is, and whether to offer one at
all — with `mail`, whether "Forgot your password?" may promise an email, and with `brand`
(`{name, logo_url, accent}`, [the-box.md](the-box.md)) what to call the box and paint it with — and with `google`, whether to draw "Continue with Google" (below).

`POST /v1/signup {org, name?, email, person, password, device?}` — no key — makes **no org**: it
keeps the sign-up in memory, mails a six-digit code by the box's own mail (`503` when it has none)
and answers `202 {email, code_expires_at}`. `POST /v1/signup/verify {email, code, device?}` makes
it: `201`, the key shape plus `slug`, the `member` (an active `admin`) and a one-use `code` for
`/?login=<code>`. A code lives 15 minutes, six wrong tries burn it (`400`; an unknown address reads
as a wrong code), and `POST /v1/signup/resend {email}` mails a new one, `202 {}` for anybody. **What
the org may do is a policy's**: verify asks `extensions.admitted` (with the orgs that email already
has here) and writes the `Quotas` as the org is made. The first door refuses: `403` sign-ups shut;
`409` a slug taken (again at verify); `400` a bad slug, email or short password; `401` an email
whose password here is another; `409` an address invited and still passwordless; `429` the sixth try
from one place in a minute. With `PINECALL_SIGNUP_KEY` set the three doors take only `Bearer` that
key (the bot shield's) and count the client as the `X-Pinecall-Client` it sends; without it
that header is never believed.

The console is served by this gateway, the same origin as every door it uses; the sign-up page is
the shield's, which calls here server to server with its key and needs no CORS at all. So no page
on the web has a reason to call a door from elsewhere, and no page is let: a CORS
header goes to exactly one caller, **Pinecall's own mobile app** — a supervisor's WebView, whose
origin is `capacitor://localhost` on iOS and `https://localhost` on Android and never this box's.
Those two origins are always allowed; `PINECALL_APP_ORIGINS` adds more, comma separated, and is
for the app's dev server (`http://localhost:5173`) on a laptop — unset, there are only the two. An
allowed origin gets itself echoed in `Access-Control-Allow-Origin` with `Vary: Origin`, on every
`/v1` door including the SSE streams, and its preflight is told `GET POST PUT PATCH DELETE`, the
headers `authorization`, `content-type`, `pinecall-env`, `pinecall-corner`, `last-event-id`,
`accept`, and ten minutes of `Max-Age` (`api/origins.py`). Any other origin gets no CORS header
at all, preflight included, and its browser refuses as before. **A list and not `*`**, because a
person's key in a page anywhere is a key some page anywhere can be written to steal; a list names
the one app that holds one. **No `Allow-Credentials`**, because the key travels as a bearer header
the app sets itself and never as a cookie a browser would send on its own. The other answer that
carries a CORS header is not a door: the widget script at `/widget/pinecall-widget.js`, which any
site loads from the gateway with `*`.

## Signing a terminal in

`pinecall login` holds no key, and the person at it has none to paste: a key is minted for a person
and kept by the browser that minted it. So the terminal and the browser meet at a **word**.

`POST /v1/login/pairings {device?}` — no key — answers `{code, expires_at}`. The terminal prints
`<gateway>/cli?c=<code>`, opens it, and asks `GET /v1/login/pairings/{code}/key` until it answers.
That door is `202` with an empty body while nobody has approved, `200 {key}` once somebody has, and
`404` once the key has been collected or the word has died — ten minutes, whichever comes first.

The browser, holding the person's key, reads `GET /v1/login/pairings/{code}` — `{device,
expires_at, answered}`, and never a key — so the card can say **what** it is about to sign in, and
then `POST /v1/login/pairings/{code}` approves it. What that mints is the TERMINAL's own key: a
fresh one for the same person, with their role's scopes, labelled as that machine, so it is
revoked on its own from the Tokens screen; `pinecall start --prod` names production per request. The browser's key never travels to the terminal, and the terminal's
never travels through the browser. **The password is typed into a page, never into a shell.**

## Signing in at the org's own identity provider

An org whose people already exist in Google Workspace, Okta or Entra does not want a second
password on this box. It wires **one OpenID Connect provider of its own** and its people sign in
there; nothing else about a member changes — the row, the role, the seat, the key and every door
are the ones above. The terminal's dance does not change either: the person signs in to the
console with their provider and then approves the terminal exactly as they always did.

**What an admin wires**, on a key with `team` — the scope that already invites a person and
changes their role, because this is saying who the org's people are:

`GET /v1/org/sso` answers `{configured, issuer, client_id, domains, role, required,
redirect_uri}` — one shape either way, an org that wired nothing answering the empty value of
each rather than leaving them out, so a page parses one envelope. **`redirect_uri` is what an
operator registers at the provider** — `https://<gateway>/v1/login/sso/callback` — and it is in
the answer rather than in a page, because a page would drift the day the box was reached by
another name. `PUT /v1/org/sso {issuer, client_id, client_secret, domains, role?, required?}`
replaces the whole configuration and answers the same shape; the issuer is fetched
(`/.well-known/openid-configuration`) before anything is kept, so a typo is `400` with nothing
written. `DELETE /v1/org/sso` is `204`, and `404` for an org that wired nothing — a typo must
never read as done. **The client secret goes in and never comes out**: it is a Fernet token under
the box's `PINECALL_VAULT_KEY`, exactly as a provider key is, and no door of this runtime answers
with one. A runtime given no vault key keeps none and answers these doors `503 no
PINECALL_VAULT_KEY: this runtime cannot keep a tenant's key`.

`domains` are the email domains the org signs in with, folded — an address outside them is refused
at the callback even when the provider vouched for it, because a tenant at Entra can hold guests
from anywhere. `role` is what an address **nobody invited** becomes: left out, nobody is
auto-provisioned and a stranger is refused. `required` is the org saying a password opens it no
longer.

**The flow**, authorization code with PKCE, and no key at any of its three doors:

`GET /v1/login/sso?org=<id or slug>[&pairing=<cli code>]` answers `302` to the provider's
authorization endpoint with `state`, `nonce` and an S256 `code_challenge`; the state, the nonce
and the verifier stay on the gateway, one use and ten minutes, beside the terminal pairings and
for the same reason. `404` when the org wired nobody, `502` when the provider does not answer, and
`429` on the sixth sign-in in a minute from one place — the rate a sign-up is held to, because
nobody has typed an address here for the throttle to count by name.

`GET /v1/login/sso/callback?code=&state=` spends the state, exchanges the code, and checks the
id_token against the issuer's JWKS — signature, `iss`, `aud`, `exp`, and the `nonce` this sign-in
minted. The address must be one the provider says it **verified**, and in one of the org's
domains. Then the member: `active` signs in; `invited` is seated active by signing in (they keep
no password — the provider is how they get in); `disabled` is `403`; an address nobody invited is
`403` unless `role` says otherwise, and auto-provisioning takes a **seat** and is `429` in the
quota's own sentence when the plan has none left. What the person lands on is `302` to
`/?login=<code>` — the very one-use login code `pinecall start` prints, spent at `POST /v1/login
{code}` for a key of the browser's own, labelled `console`, with what their role opens. **No key is ever in a URL.** With `pairing`, the landing is `/cli?c=<code>&login=…`
instead: the card that signs the terminal in, reached holding a key.

`POST /v1/login/sso/discover {email}` answers `{orgs: [{org, slug, name}]}` — the orgs whose
domains match that address's and that wired a provider. It says nothing about whether anybody
answers to the address, shares `/v1/login`'s throttle, and answers `{orgs: []}` on a box that
keeps no provider at all.

**What a password does then.** Wiring a provider is not requiring one: both ways in until the org
sets `required`. With it, `POST /v1/login` answers `401 <org> signs in with its identity provider:
open /v1/login/sso?org=<org> instead of a password` — and **only once the password has matched**
and the row has been found, so a wrong password is the one `401` it always was and a stranger
learns nothing about who is a member of what. A person who belongs to two orgs and names none
lands in the one their password still opens; the sentence is for somebody every org of whose signs
in with a provider.

**The break-glass is the box's.** An org that lost its provider — a tenant renamed, a secret
rotated on a Friday — has nobody inside who can turn `required` off, because the admin who would
is the person locked out. So `GET /v1/ops/orgs/{org}/sso` reads what one org is wired to and
`PUT /v1/ops/orgs/{org}/sso/required {required}` turns it off, on the operator's key ([operator-api.md](operator-api.md#authentication))
(`pinecall-runtime orgs sso <org> --off`). Turning it back **on** is the org's own door: an
operator who could would be an operator deciding how a tenant's people sign in. Losing the vault
key has the same effect by itself — no secret can be read, so no org signs in with a provider and
a password opens every one of them again.

## Signing in with Google, box-wide

**"Continue with Google"** is the operator's, for every org at once ([the-box.md](the-box.md)):
`GET /v1/login/google[?pairing=]` answers `302` to Google — `openid email profile`, issuer
`https://accounts.google.com`, authorization code with PKCE, state and nonce, exactly as an org's
own provider is asked (above); `404 this box signs in with no Google: …` while nobody wired one,
and the sixth sign-in in a minute from one place is `429`, as the SSO's is. Google sends the person to
`GET /v1/login/google/callback?code=&state=`, where the id_token is checked and the address it
carries — **`email_verified` required**, then trimmed and lower-cased — is matched against the
**members of every org**: a person is their email on this box, and Google vouching for the
address is what the link in an invitation would have proved. So:

- an **active** member lands, as a password login with no org does, in the **oldest** org of
  theirs that is not disabled and does not sign in with its own required provider — `302
  /?login=lc_…`, the one-use code the console spends for a key of the browser's own, and the org
  switch does the rest (`?pairing=` sends them to `/cli` instead, for a terminal waiting);
- a member still **invited** is seated `active` by it, in every org of theirs that a Google
  sign-in may enter — they keep no password, as with an org's own provider, and may still open
  the invitation link to choose one;
- **nobody** — no row anywhere — is `302 /?refused=<email> is not a member of any org on this
  box: an admin of your org has to invite that address before Google can sign it in`; disabled
  in every org, `…is disabled in every org of theirs here`; only in orgs whose own provider is
  `required`, `…belongs to an org that signs in with its own identity provider: open
  /v1/login/sso?org=… instead`. An unverified address and a refusal at Google are `?refused=` too.

Every refusal past the handshake is a redirect and not a body, because a person in a browser
between two redirects reads the sign-in page and not JSON; a state nobody minted, or an org's
own SSO state, is the one `400`. An org whose SSO is `required` is never entered this way, and
nothing here makes a member: an address nobody invited is refused, whatever Google says.

### Registering this gateway at the provider

One redirect URI, `https://<gateway>/v1/login/sso/callback`, and one client per org. What each
vendor calls its parts:

| | issuer | where the client is made |
|---|---|---|
| Google Workspace | `https://accounts.google.com` | Cloud console → APIs & Services → Credentials → OAuth client ID, type **Web application**; the URI goes in *Authorized redirect URIs* |
| Okta | `https://<tenant>.okta.com` (or the custom authorization server's own) | Applications → Create App Integration → OIDC → **Web Application**; *Sign-in redirect URIs* |
| Microsoft Entra ID | `https://login.microsoftonline.com/<tenant-id>/v2.0` | App registrations → New registration → Redirect URI, platform **Web**; the secret is under *Certificates & secrets* |

The client must be a **confidential** one — this gateway holds a secret and exchanges the code
server to server — and it needs the `openid`, `email` and `profile` scopes, which is what the
sign-in asks for. Nothing else has to be turned on: no directory read, no group claim, no SAML.
