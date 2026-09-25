# Every door, in one table

The index of [gateway-api.md](gateway-api.md): one line per door, method and path, and what it
is for. The prose, the shapes and the refusals are on that page and in the pages it names. Every
door that takes a key, both sockets included, reads `pinecall-env: sandbox|production`: the world
a person's key works in for this request (none is the sandbox; production only with production
access, `403` otherwise), and on a server's token only its own world, `403` for the other. Every
HTTP door then reads `pinecall-corner: <member id>`: an admin's key, in the sandbox, answered in
that colleague's corner.

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
| `GET` | `/v1/keys` | the org's tokens by fingerprint: every server's, and your own person keys (every person's with `keys`), who made each and when it was last used — any key |
| `POST` | `/v1/keys` | a server's token `{label, env}`, answered once, `pc_live_`/`pc_test_`: a person's key with `app`, production only with production access |
| `POST` | `/v1/keys/{fingerprint}/revoke` | stop your own key, a token you made, or any with `keys`; the row and its history stay |
| `GET` | `/v1/sessions?limit=&q=&agent=&channel=&before=` | the newest calls across every agent, in the reader's corner, filtered and paged, each with its verdict and flags — [console-api.md](console-api.md) |
| `GET` | `/v1/insights?day=` | one day of the reader's corner — calls, resolved rate, median e2e, spend, doors, agents — and the month's budget, UTC — `calls` |
| `GET` · `PUT` | `/v1/org/judging` | whether the org's calls are judged at hang-up, and the box's ceiling; turned with `usage` |
| `GET` | `/v1/calls/{call}/judging` | the worker's, at hang-up: whether that call's org judges — `app` |
| `GET` | `/v1/events` | SSE, live only: the org's floor changing — an agent held, a call ringing, up, over, a call asking for a person, a person on the line |
| `GET` | `/v1/members` · `POST` | the org's people; invite one, the token once and `mailed` — or none, for a person who already has a password here: seated at once |
| `PATCH` | `/v1/members/{id}` | role, agents, standing, `production`; disabled revokes their keys; `409` taking production from an admin |
| `DELETE` | `/v1/members/{id}` | out of the org for good: keys revoked, row and links gone, the seat free — `team`; `409` for yourself and for the last active admin |
| `POST` | `/v1/members/{id}/reset` | a one-use link that sets an active member's password, the token once, and `mailed` — `team` |
| `POST` | `/v1/invitations/{token}` | accept with a password: active, and the first key |
| `POST` | `/v1/login` | a key for a person and a device: email, password, the org when they have several — or a code |
| `POST` | `/v1/login/orgs` | which orgs an email and password sign in to, minting nothing — no key, throttled like the login |
| `POST` | `/v1/login/reset` | a forgotten password: `202` whoever asks, and a one-use link mailed where one can be — no key, throttled like the login |
| `GET` | `/v1/login/orgs` | every org this key's person belongs to, and which one the key opens; for an operator of the box, every org there is, `member: false` and `role: "operator"` where they are none — a person's key |
| `POST` | `/v1/login/org` | the same person's key in another org of theirs; an operator is let into ANY org on a production key with an admin's scopes, `subject` `operator:<email>`, no member row and no seat — a person's key |
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
| `GET` | `/v1/whoami` | the org as an id AND as the `slug` its people type, the key's id, its label, the world this request runs in (`env`), whether it may act in `production`, its `scopes`, whose it is (`subject`, `name`), whether that person runs the box (`operator`) and whether they are inside an org they are no member of (`visiting`) |
| `GET` | `/v1/ops/whoami` | **the box's own**: that this key is the operator's, the version, the domain, and the `name` and `org` of the person holding it — null for the box's own key; what the console's Box screens prove their key at |
| `GET` · `PUT` · `DELETE` | `/v1/ops/mail` | **the box's own**: the mail server the box posts through, stored here over the environment's — `source` says which; never the password. [the-box.md](the-box.md) |
| `POST` | `/v1/ops/mail/test` | **the box's own**: one letter through the box's mailbox, waited for — `{sent, error}` |
| `GET` | `/v1/ops/signin` | **the box's own**: every box-wide provider — `{google: {configured, client_id, redirect_uri}}` |
| `PUT` · `DELETE` | `/v1/ops/signin/google` | **the box's own**: the OAuth client at Google, its secret sealed; Google's discovery checked before anything is kept |
| `GET` | `/v1/ops/events` | **the box's own**: SSE, live only — every org's floor at once, each frame `{org, entry}`. [the-boxs-floor.md](the-boxs-floor.md) |
| `GET` · `PUT` | `/v1/ops/brand` | **the box's own**: what the letters and the sign-in page are called and painted with — `{name, logo_url, accent}` |
| `GET` | `/v1/ops/orgs/{org}/sso` | **the box's own**: which provider one org signs in with, never its secret |
| `PUT` | `/v1/ops/orgs/{org}/sso/required` | **the box's own**: the break-glass — a password opens that org again while its provider is down. Never the other way |
| `GET` · `POST` | `/v1/ops/orgs/{org}/members` | **the box's own**: an org's people and how many hold a seat; invite one — the first admin, where sign-ups are shut — the token once. Never a change |
| `PUT` | `/v1/ops/orgs/{org}/members/{id}/operator` | **the box's own**: whether this member runs the box; false takes it back at once |
| `DELETE` | `/v1/ops/orgs/{org}/members/{id}` | **the box's own**: that person out of the org for good, under the tenant door's rules less "yourself" — `409` for its last active admin |
| `GET` | `/v1/agents` | the agents this gateway is holding for your org |
| `GET` | `/v1/apps` | the processes holding them right now, one per socket: agents, world, the machine (`host`, from `agent.register`), address, SDK, whose corner, since when |
| `POST` | `/v1/apps/{app}/stop` | `app`: tell that process it was stopped (`error` code `stopped`) and close its socket; it exits instead of reconnecting — a supervisor (systemd, pm2) starts it again |
| `GET` | `/v1/agents/{slug}/config` | what it declared, its world's settings on it — `app` or `calls` |
| `GET` | `/v1/agents/{slug}/line` | whose terminal a RING lands in, and who else could take it — `calls` |
| `POST` | `/v1/agents/{slug}/line` | claim it for this key's corner — `app`; 409 with no app of yours running |
| `DELETE` | `/v1/agents/{slug}/line` | release it; whoever is still holding the agent picks it up — `app` |
| `PUT` | `/v1/line/from` | this phone's calls reach this key's corner, in whatever agent it holds — the sandbox number's, and a production number's too — `app`, set from the sandbox only |
| `GET` | `/v1/line/numbers` | the org's production phone numbers and the agent each reaches: what a developer's own phone dials to reach their copy — `app`, a key naming a person, from the sandbox |
| `DELETE` | `/v1/line/from` | stop answering your own calls; they fall back to the line — `app` |
| `GET` | `/v1/agents/{slug}/pipeline` | what it hears, decides and speaks with, and what that cost — [pipeline-api.md](pipeline-api.md) |
| `GET` | `/v1/agents/{slug}/pipeline/hold-audio` | which melody it plays while a tool runs — the runtime's own, `off`, or an uploaded clip with its name, its length and its hash — `pipeline` |
| `PUT` | `/v1/agents/{slug}/pipeline/hold-audio` · `?name=` | a file of yours as that melody: the BODY is the file, no multipart, converted once to Ogg Opus; `413` over 20 MB, `400` for what is no melody — `pipeline` |
| `GET` | `/v1/agents/{slug}/pipeline/hold-audio/audio` | the melody itself, `audio/ogg`, to listen to before a caller does; `404` while the agent plays none — `pipeline` |
| `PUT` | `/v1/agents/{slug}/pipeline/hold-audio/played` | `{played: "default"|"off"}`: the runtime's melody back, or silence — the uploaded clip forgotten either way — `pipeline` |
| `GET` · `PUT` | `/v1/agents/{slug}/settings` | what the org set over the class — vendors, models, the opening, the cut of a turn, what is remembered, what it knows by heart, the bases — per world, per corner, a version a row: yours, the team's, production's — a set writes the request's world, production's directly — `pipeline` or `words`; `words` sets the opening's words and what is remembered and is refused the rest by name — [settings-api.md](settings-api.md) |
| `GET` | `…/settings/history` · `…/settings/diff` | one corner's versions, newest first; this corner against the team's or production's — `pipeline` or `words` |
| `POST` | `…/settings/rollback` | one version back as the next one — `pipeline` |
| `GET` | `/v1/calls/{call}/settings` | the exact settings and lexicon a call ran on, by the versions its head row kept — `calls` |
| `GET` · `PUT` | `/v1/lexicon` · `GET …/history` | the org's words — how the voice says them, what the ears must know — laid over every agent's own, in the request's world — `pipeline` or `words` |
| `GET` · `PUT` | `/v1/agents/{slug}/widget` | how the widget presents the agent — title, tagline, greeting, accent, autostart — per world; read with `talk`, set with `pipeline` |
| `POST` | `/v1/agents/{slug}/dev/{family}/{verb}` · `?app=` | a console's ask, relayed to the app standing in the agent's directory — `chat`, `knowledge`, `memory`, `view` or `evals` by family; [dev-verbs.md](dev-verbs.md) |
| `GET` | `/v1/agents/{slug}/provider-keys` | the org's own vendor keys, **in the clear**: the worker's door, see §6 |
| `GET` | `/v1/agents/{slug}/rings-for?caller=` | whose sandbox copy a production ring from this phone belongs to, or null: production's — the worker's, `app` |
| `GET` | `/v1/agents/{slug}/hold-audio` | the worker's: what the call being built plays while a tool runs, in the call's corner (`?org=&env=&holder=`) — `app` or `calls` |
| `GET` | `/v1/agents/{slug}/hold-audio/audio` | the worker's: the clip's bytes, fetched once per box per hash and kept on disk; `404` when the agent plays none — `app` or `calls` |
| `GET` | `/v1/agents/{slug}/outbound-trunk` · `?to=&call=` | the worker's: the trunk a second leg on a live call is dialled through — a warm transfer, `room.invite`. The number passes the org's dial guards first and lands in the same ledger: `400` a shape, `429` a window; `{trunk: null}` when the org has no trunk — `app` or `calls` |
| `GET` | `/v1/agents/{slug}/sessions` | one line per call, in the reader's corner — the same filters |
| `GET` | `/v1/agents/{slug}/calls` | every call of the agent, as a log |
| `GET` | `/v1/calls/{call}/events` | one call's log: a page, or SSE |
| `GET` | `/v1/calls/{call}/state` | the call reduced |
| `GET` | `/v1/calls/{call}/recording` | the audio, seekable. A written (chat) call keeps none: `404`, `call … kept no recording: its call.summary points at none` |
| `POST` | `/v1/calls/{call}/listen` · `/supervise` | a seat |
| `POST` | `/v1/calls/{call}/verbs` | one supervisor verb, from a key of the org whose call it is · `403` another's, `404` no live call, `409` it is over |
| `POST` | `/v1/tokens` | a room token for a browser — `503` and `fleet.full` when every worker is full |
| `POST`·`GET` | `/v1/callbacks` | a number to call back when the fleet was full, and the list of them |
| `GET` | `/v1/routes` | the numbers and doors your org answers |
| `PUT`·`DELETE`·`GET` | `/v1/provider-keys[/{vendor}]` | the org's own vendor accounts — `providers` |
| `GET` | `/v1/providers` | every vendor this build runs, which are ready on this box and which want a key, the vendor each stage runs on when nobody chose (`defaults`), the models this build vouches for under `<modality>/<vendor>` (`models`) and the curated voices — `providers` |
| `GET` | `/v1/voices?tts=&language=` | a voice vendor's own voices in that language, each with the id the `voice` setting takes, its name, gender, country and accent — Cartesia asked of Cartesia, page by page, on the org's own key or the box's; ElevenLabs the names this build curates · `404` a vendor whose catalogue is not read, `409` no key for it, `502` the vendor did not answer — `pipeline` |
| `POST` | `/v1/voices/sample` | `{tts, voice, model?, language?, text}` said by that vendor's plugin exactly as a call would build it, answered as `audio/wav` with `Server-Timing: first-audio;dur=…, total;dur=…`; the text is at most 400 characters · `409` no key, `422` a voice that is a typo, `502` the vendor refused it — `pipeline` |
| `GET` | `/v1/personas` · `PUT`·`DELETE …/{name}` | the org's synthetic callers — a goal, a manner, the facts they may state, how they are played (`llm` · `tts` · `voice`, the agent's own three words, `422` for one this box does not have) and when they accept the call (`accepts_when` · `declines_when`, which the `persona` judge reads at hang-up) — one list an org, whichever agent and whichever world asks; `evals` |
| `GET` | `/v1/personas/{name}/runs?limit=&before=` | that caller's simulations in the key's corner, newest first: the call, the agent, when, its turns, how it ended, its outcome, what it cost and how the judges answered. Paged as the sessions list is; `404` for a name this org never wrote — `evals` |
| `PUT`·`GET`·`DELETE` | `/v1/knowledge[/{base}]` · `POST …/eval` · `GET /v1/knowledge/attached` · `GET`·`PUT`·`DELETE …/{base}/files/{path}` | the bases the agent searches, in the request's world — production's pushed there directly; a push answers the chunks it made; which agents read which; a base's files listed, read, put and taken out one at a time |
| `GET`·`DELETE` | `/v1/contacts/{contact}/memory` · `POST /v1/contacts/memory/eval` | what it keeps about a person, in the key's world |
| `GET` | `/v1/agents/{slug}/threads?after=` · `/threads/{contact}` | the inbox: an agent's calls by contact, what this person has not read, and one thread merged — `calls` |
| `POST` | `/v1/agents/{slug}/threads/{contact}/read` · `/messages` | mark a thread read — `calls`; say something on the open WhatsApp conversation — `talk` |
| `GET` | `/v1/agents/{slug}/memory?after=&q=` | the current facts the agent's calls taught, across contacts — `memory` |
| `GET` | `/v1/memory?after=&q=` | the same across every agent of the org, each fact with its `agent` — `memory` |
| `DELETE` | `/v1/memory/facts/{id}` | end one fact, bi-temporally: the row stays, superseded — `memory` |
| `POST` | `/v1/agents/{slug}/memory/extraction` | what a hang-up makes of a call |
| `POST` | `/v1/evals/run` · `GET /v1/evals/runs[/{id}]` · `POST /v1/evals/replay/{call}` · `/v1/evals/judge/{call}` | the suites, ring 3, and the judges over a finished call nobody judged |
| `POST` | `/v1/evals/caller` · `/v1/evals/voice` | the improvising caller — on the persona's own `llm` when it set one — and a spoken eval, in the persona's own `tts` and `voice` when it set them; `422` for a word this box does not have |
| `POST` | `/v1/calls` · `/v1/calls/{call}/events` · `/sealed` · `/tools` · `/lookup` · `/remember` · `GET /commands` | the worker's own doors; `/lookup` is also the app's own `this.knowledge.search` — `app` |
| `POST`·`GET` | `/v1/fleet/heartbeat` · `/v1/fleet/standing` | the fleet's: what a worker holds, and whether all are full. A key holding `app` AND `fleet` — what the box mints for its worker |
| `GET`·`POST` | `/v1/whatsapp/webhook` | Meta's |
| `GET` | `/.well-known/pinecall` | what this gateway is before anybody holds a key: version, `cloud`, `signup`, `min_password`, `mail`, `brand`, `google` — no key |
| `GET` | `/` | the console — no key to load, it proves its own |
| `GET` | `/widget/pinecall-widget.js` | the widget, for any site to load: `Access-Control-Allow-Origin: *`, the one answer to any origin; under `/v1` only the mobile app's origins are echoed ([people.md](people.md)) |
| | `/v1/ops/*` | the operator's: the box's own key, or the key of a person the box made an operator — [operator-api.md](operator-api.md) |

`GET /openapi.json` is the generated schema of all of it — with `/v1/docs` and `/v1/redoc`, the two
pages that render it, all three open with no key, as an API's schema usually is — and
`pinecall-runtime doctor` on the box
says which of these doors can actually answer today.
