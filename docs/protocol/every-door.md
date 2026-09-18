# Every door, in one table

The index of [gateway-api.md](gateway-api.md): one line per door, method and path, and what it
is for. The prose, the shapes and the refusals are on that page and in the pages it names. Every
HTTP door that takes a key also reads `pinecall-corner: <member id>`: an admin's key, in the
sandbox, answered in that colleague's corner.

| | | |
|---|---|---|
| `WS` | `/v1/apps` | the app socket: hold an agent, answer its tools |
| `WS` | `/v1/chat?agent=` | one text caller |
| `WS` | `/v1/attach?call=&token=` | a seat's live log, and the verbs back |
| `GET` | `/v1/usage?after=&limit=` | the org's metered rows, totals and cursor — `usage` |
| `GET` | `/v1/numbers` | the org's doors in the key's world, each with its source — `numbers` |
| `PUT` · `GET` · `DELETE` | `/v1/carrier` | the org's carrier: a Twilio account or a SIP peer, sealed under the vault key, named never secret |
| `GET` | `/v1/numbers/available` | what the carrier account owns, and which of it is imported |
| `POST` | `/v1/numbers` · `?dry_run=true` | import one number: the carrier's trunk pointed here, the SFU's trunk admitting it, the route — or the plan alone |
| `POST` | `/v1/numbers/buy` · `?dry_run=true` | buy one number on the box's own Twilio into the org, wired as an import, capped by the `numbers` quota — or the plan alone |
| `PUT` | `/v1/numbers/{number}/env` | move a number to the other world — one row, the carrier untouched. What makes a staging run cost nothing — `numbers` |
| `DELETE` | `/v1/numbers/{number}` | let a number go: the route and the admission; the carrier untouched |
| `GET` | `/v1/carrier/outbound` | whether the org can place a call at all, one sentence per thing missing, and the guards it dials under — `numbers` |
| `POST` | `/v1/carrier/outbound` · `?dry_run=true` | provision the trunk the org dials THROUGH — Twilio's termination and a credential list, or the peer the tenant declared, then the SFU's outbound trunk — or the plan alone |
| `POST` | `/v1/agents/{slug}/dial` | place a call as this agent: `202` with the call it became, after the guards — `talk` |
| `GET` | `/v1/keys` · `POST` | the org's own API keys by fingerprint; mint one for a machine, answered once — `keys` |
| `POST` | `/v1/keys/{fingerprint}/revoke` | stop one of the org's keys; the row and its history stay |
| `POST` | `/v1/login/env` | the same person's key for the other world, with what their role opens there |
| `GET` | `/v1/sessions?limit=&q=&agent=&channel=&before=` | the newest calls across every agent, in the reader's corner, filtered and paged, each with its verdict and flags — [console-api.md](console-api.md) |
| `GET` | `/v1/insights?day=` | one day of the reader's corner — calls, resolved rate, median e2e, spend, doors, agents — and the month's budget, UTC — `calls` |
| `GET` · `PUT` | `/v1/org/judging` | whether the org's calls are judged at hang-up, and the box's ceiling; turned with `usage` |
| `GET` | `/v1/calls/{call}/judging` | the worker's, at hang-up: whether that call's org judges — `app` |
| `GET` | `/v1/events` | SSE, live only: the org's floor changing — an agent held, a call ringing, up, over |
| `GET` | `/v1/members` · `POST` | the org's people; invite one, the token once and `mailed` — or none, for a person who already has a password here: seated at once |
| `PATCH` | `/v1/members/{id}` | role, agents, standing; disabled revokes their keys |
| `DELETE` | `/v1/members/{id}` | out of the org for good: keys revoked, row and links gone, the seat free — `team`; `409` for yourself and for the last active admin |
| `POST` | `/v1/members/{id}/reset` | a one-use link that sets an active member's password, the token once, and `mailed` — `team` |
| `POST` | `/v1/invitations/{token}` | accept with a password: active, and the first key |
| `POST` | `/v1/login` | a key for a person and a device: email, password, the org when they have several — or a code |
| `POST` | `/v1/login/orgs` | which orgs an email and password sign in to, minting nothing — no key, throttled like the login |
| `POST` | `/v1/login/reset` | a forgotten password: `202` whoever asks, and a one-use link mailed where one can be — no key, throttled like the login |
| `GET` | `/v1/login/orgs` | every org this key's person belongs to, and which one the key opens; for an operator of the box, every org there is, `member: false` and `role: "operator"` where they are none — a person's key |
| `POST` | `/v1/login/org` | the same person's key in another org of theirs, in the same world; an operator is let into ANY org on a production key with an admin's scopes, `subject` `operator:<email>`, no member row and no seat — a person's key |
| `POST` | `/v1/login/codes` | a one-use code a key holder mints for a browser |
| `POST` | `/v1/login/pairings` | a word a terminal prints, so a person signs it in from a browser — no key |
| `GET` | `/v1/login/pairings/{code}` | what the card is about to approve: which terminal, and whether it is answered — no key |
| `POST` | `/v1/login/pairings/{code}` | sign that terminal in as the person this browser is — any person's key |
| `GET` | `/v1/login/pairings/{code}/key` | the terminal collects its key, once. 202 while nobody has approved — no key |
| `GET` · `PUT` · `DELETE` | `/v1/org/sso` | the org's OpenID provider: the issuer, the client, the domains it admits, who it seats and whether a password still opens it — `team`. The client secret goes in and never comes out; 503 with no vault key |
| `GET` · `PUT` · `DELETE` | `/v1/org/mail` | the org's own SMTP account its letters go out through, and how the last one went — `team`. The password goes in and never comes out; 503 with no vault key |
| `POST` | `/v1/org/mail/test` | one test letter, waited for: `{sent, error}` — `team`; 409 when nothing can send |
| `GET` | `/v1/login/sso?org=&pairing=` | 302 to that org's provider, with state, nonce and a PKCE challenge — no key |
| `GET` | `/v1/login/sso/callback?code=&state=` | the code exchanged and the id_token checked; 302 to `/?login=<code>`, so no key is ever in a URL — no key |
| `GET` | `/v1/login/google[?pairing=]` | box-wide "Continue with Google": 302 to Google — no key, throttled like the SSO; 404 while nobody wired one |
| `GET` | `/v1/login/google/callback?code=&state=` | the address matched against every org's members: 302 `/?login=<code>` for a member, `/?refused=<why>` for nobody — no key |
| `POST` | `/v1/login/sso/discover` | which orgs an address's domain signs in to with a provider; says nothing about who exists — no key, throttled like the login |
| `POST` | `/v1/signup` | where `PINECALL_SIGNUP` is on, off by default: a new org allowed what its gateway's policy says, its admin active, their first key and a login code |
| `GET` | `/v1/whoami` | the org as an id AND as the `slug` its people type, the key's id, its label, the world it opens (`env`), its `scopes`, whose it is (`subject`, `name`), whether that person runs the box (`operator`) and whether they are inside an org they are no member of (`visiting`) |
| `GET` | `/v1/ops/whoami` | **the box's own**: that this key is the operator's, the version, the domain, and the `name` and `org` of the person holding it — null for the box's own key; what the `/admin` page proves its key at |
| `GET` · `PUT` · `DELETE` | `/v1/ops/mail` | **the box's own**: the mail server the box posts through, stored here over the environment's — `source` says which; never the password. [the-box.md](the-box.md) |
| `POST` | `/v1/ops/mail/test` | **the box's own**: one letter through the box's mailbox, waited for — `{sent, error}` |
| `GET` | `/v1/ops/signin` | **the box's own**: every box-wide provider — `{google: {configured, client_id, redirect_uri}}` |
| `PUT` · `DELETE` | `/v1/ops/signin/google` | **the box's own**: the OAuth client at Google, its secret sealed; Google's discovery checked before anything is kept |
| `GET` · `PUT` | `/v1/ops/brand` | **the box's own**: what the letters and the sign-in page are called and painted with — `{name, logo_url, accent}` |
| `GET` | `/v1/ops/orgs/{org}/sso` | **the box's own**: which provider one org signs in with, never its secret |
| `PUT` | `/v1/ops/orgs/{org}/sso/required` | **the box's own**: the break-glass — a password opens that org again while its provider is down. Never the other way |
| `GET` · `POST` | `/v1/ops/orgs/{org}/members` | **the box's own**: an org's people and how many hold a seat; invite one — the first admin, where sign-ups are shut — the token once. Never a change |
| `PUT` | `/v1/ops/orgs/{org}/members/{id}/operator` | **the box's own**: whether this member runs the box; false takes it back at once |
| `DELETE` | `/v1/ops/orgs/{org}/members/{id}` | **the box's own**: that person out of the org for good, under the tenant door's rules less "yourself" — `409` for its last active admin |
| `GET` | `/v1/agents` | the agents this gateway is holding for your org |
| `GET` | `/v1/agents/{slug}/config` | what it declared, overrides applied — `app` or `calls` |
| `GET` | `/v1/agents/{slug}/line` | whose terminal a RING lands in, and who else could take it — `calls` |
| `POST` | `/v1/agents/{slug}/line` | claim it for this key's corner — `app`; 409 with no app of yours running |
| `DELETE` | `/v1/agents/{slug}/line` | release it; whoever is still holding the agent picks it up — `app` |
| `PUT` | `/v1/line/from` | this phone's calls reach this key's corner, in whatever agent it holds — the sandbox number's, and a production number's too — `app`, set from the sandbox only |
| `GET` | `/v1/line/numbers` | the org's production phone numbers and the agent each reaches: what a developer's own phone dials to reach their copy — `app`, a key naming a person, from the sandbox |
| `DELETE` | `/v1/line/from` | stop answering your own calls; they fall back to the line — `app` |
| `GET` | `/v1/agents/{slug}/pipeline` · `PUT …/pipeline/overrides` | what it runs on, and the six knobs — [pipeline-api.md](pipeline-api.md) |
| `GET` · `PUT` | `/v1/agents/{slug}/widget` | how the widget presents the agent — title, tagline, greeting, accent, autostart — per world; read with `talk`, set with `pipeline` |
| `POST` | `/v1/agents/{slug}/dev/{family}/{verb}` · `?app=` | a console's ask, relayed to the app standing in the agent's directory — `talk`, `knowledge`, `memory` or `evals` by family; [dev-verbs.md](dev-verbs.md) |
| `GET` | `/v1/agents/{slug}/provider-keys` | the org's own vendor keys, **in the clear**: the worker's door, see §6 |
| `GET` | `/v1/agents/{slug}/rings-for?caller=` | whose sandbox copy a production ring from this phone belongs to, or null: production's — the worker's, `app` |
| `GET` | `/v1/agents/{slug}/sessions` | one line per call, in the reader's corner — the same filters |
| `GET` | `/v1/agents/{slug}/calls` | every call of the agent, as a log |
| `GET` | `/v1/calls/{call}/events` | one call's log: a page, or SSE |
| `GET` | `/v1/calls/{call}/state` | the call reduced |
| `GET` | `/v1/calls/{call}/recording` | the audio, seekable. A written (chat) call keeps none: `404`, `call … kept no recording: its call.summary points at none` |
| `POST` | `/v1/calls/{call}/listen` · `/supervise` | a seat |
| `POST` | `/v1/calls/{call}/verbs` | one supervisor verb |
| `POST` | `/v1/tokens` | a room token for a browser — `503` and `fleet.full` when every worker is full |
| `POST`·`GET` | `/v1/callbacks` | a number to call back when the fleet was full, and the list of them |
| `GET` | `/v1/routes` | the numbers and doors your org answers |
| `PUT`·`DELETE`·`GET` | `/v1/provider-keys[/{vendor}]` | the org's own vendor accounts — `providers` |
| `GET` | `/v1/providers` | every vendor this build runs, which are ready on this box and which want a key, the defaults and the curated voices — `providers` |
| `PUT`·`GET`·`DELETE` | `/v1/knowledge[/{base}]` · `POST …/eval` | the base the agent answers from, in the key's world |
| `GET`·`DELETE` | `/v1/contacts/{contact}/memory` · `POST /v1/contacts/memory/eval` | what it keeps about a person, in the key's world |
| `GET` | `/v1/agents/{slug}/threads?after=` · `/threads/{contact}` | the inbox: an agent's calls by contact, what this person has not read, and one thread merged — `calls` |
| `POST` | `/v1/agents/{slug}/threads/{contact}/read` · `/messages` | mark a thread read — `calls`; say something on the open WhatsApp conversation — `talk` |
| `GET` | `/v1/agents/{slug}/memory?after=&q=` | the current facts the agent's calls taught, across contacts — `memory` |
| `GET` | `/v1/memory?after=&q=` | the same across every agent of the org, each fact with its `agent` — `memory` |
| `DELETE` | `/v1/memory/facts/{id}` | end one fact, bi-temporally: the row stays, superseded — `memory` |
| `POST` | `/v1/agents/{slug}/memory/extraction` | what a hang-up makes of a call |
| `POST` | `/v1/evals/run` · `GET /v1/evals/runs[/{id}]` · `POST /v1/evals/replay/{call}` · `/v1/evals/judge/{call}` | the suites, ring 3, and the judges over a finished call nobody judged |
| `POST` | `/v1/evals/caller` · `/v1/evals/voice` | the improvising caller, and a spoken eval |
| `POST` | `/v1/calls` · `/v1/calls/{call}/events` · `/sealed` · `/tools` · `/lookup` · `/remember` · `GET /commands` | the worker's own doors |
| `POST`·`GET` | `/v1/fleet/heartbeat` · `/v1/fleet/standing` | the fleet's: what a worker holds, and whether all are full. A key holding the `fleet` scope only |
| `GET`·`POST` | `/v1/whatsapp/webhook` | Meta's |
| `GET` | `/.well-known/pinecall` | what this gateway is before anybody holds a key: version, `cloud`, `signup`, `min_password`, `mail`, `brand`, `google` — no key |
| `GET` | `/`, `/admin` | the console, and the operator's page — no key to load, each proves its own |
| `GET` | `/widget/pinecall-widget.js` | the widget, for any site to load: `Access-Control-Allow-Origin: *`, the one CORS answer |
| | `/v1/ops/*` | the operator's: the box's own key, or the key of a person the box made an operator — [operator-api.md](operator-api.md) |

`GET /openapi.json` is the generated schema of all of it, and `pinecall-runtime doctor` on the box
says which of these doors can actually answer today.
