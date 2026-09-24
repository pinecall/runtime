# Changelog

All notable changes to `pinecall`, the runtime. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); version numbers and tags are the
maintainer's call, so everything sits under Unreleased until one is cut.

## [Unreleased]

### Added
- **The org's floor tells when a call wants a person.** `GET /v1/events` now also carries
  `attention.requested` · `attention.answered` and `supervisor.took_over` · `supervisor.released`.
- **`GET /v1/ops/events`, the box's floor.** The operator's SSE: every org's floor on one stream,
  each frame `{org, entry}` (protocol `BoxEvent`, 0.6.6), live only. What a notifier serving the
  whole box reads, where one org's key would need a stream per org.
- **The mobile app may call `/v1` from its WebView.** `capacitor://localhost` and
  `https://localhost` get their origin echoed with `Vary: Origin` and their preflight answered;
  `PINECALL_APP_ORIGINS` adds a dev server, comma separated. Any other origin still gets no CORS
  header, and nothing says `Allow-Credentials`.

### Security
- **A leg dialled into a live call passes the org's dial guards.** A warm transfer and
  `room.invite` go out on the org's own carrier, and until now neither passed the fences
  `POST /v1/agents/{slug}/dial` passes: an agent with a number in its class dialled as often as it
  liked and left no row anywhere. The worker cannot dial without a trunk and cannot have one
  without the number passing `GET /v1/agents/{slug}/outbound-trunk?to=&call=` — the shape check,
  the per-minute and per-day windows, and the same `dials` ledger. The stranger fence stays a cold
  dial's: a colleague an agent transfers to has no reason to have ever rung the org
  (`orgs/guards.py:a_second_leg`). A refusal reaches the caller's log as the guard's own sentence,
  so `call.transferred` says `dial.too_fast` and the agent can say something true.

### Fixed
- **A media plane that lost its trunks gets them back, and says so.** livekit-sip keeps every
  trunk and dispatch rule in Redis; the box ran Redis with no volume and no persistence, and on
  2026-09-22 a recreated container came up empty — three numbers stopped ringing, every dial and
  every warm transfer answered `requested sip trunk does not exist`, and `GET /v1/carrier/outbound`
  still read `ready`. Now: Redis persists (`pinecall-redis.volume`, `--appendonly yes`); the
  gateway asks the SFU for every trunk the tables describe at each start (`api/rebuilding.py`,
  per org, nothing doubled) and refreshes the row of an outbound trunk that came back under a new
  id; the worker's `outbound-trunk` door and the carrier's `ready` ask the SFU by name instead of
  trusting the row.

### Added
- **The line's six commands run.** `call.transfer` sends a phone caller on with a REFER, or dials
  the destination into a browser caller's own room over the org's outbound trunk and falls silent
  once they answer; `call.attention` puts the caller on hold until a supervisor takes the line or
  the wait the app asked for runs out; `call.hold` · `call.unhold` hold the line with the melody;
  `call.dtmf` sends touch tones down the caller's leg; `call.callback` writes the number to ring
  back into the call's log, which `GET /v1/callbacks` already lists. A written conversation refuses
  the ones that need a line, by name, and asks for a person instead. `docs/protocol/the-line.md`.
- **`GET /v1/agents/{slug}/outbound-trunk`**, the worker's: the trunk a second leg on a live call
  is dialled through. `room.invite` never dialled anybody before it — the call was built with no
  trunk at all — and a warm transfer needs the same one.
- **A tool that answers while a supervisor holds the line produces no reply**, spoken or written:
  the result is in the log and in the history, and the model does not talk over the person.

### Security
- **A key grants what it holds.** `team` opened every role to whoever held it: a manager could
  invite an admin, PATCH a colleague or their own row to one, or wire SSO seating a whole domain as
  admins, and hold the org by the next login. `POST`/`PATCH /v1/members` and `PUT /v1/org/sso` now
  refuse `403` a role whose preset opens a door the asking key does not (`auth/granting.py`,
  `this key does not open everything <role> would: …`), production access from a key that has
  none (`… has no production access, and cannot give it`), and `409` a change to one's own role
  or switch. A key naming nobody — a server's, the box's, an operator's visit — grants as before.
- **One org's admin never holds the link that sets another org's person's password.** An
  invitation's or a reset's token is in the answer only for an address that is this org's alone;
  for somebody who is also another org's it is posted to them (`mailed`) and to nobody else — a
  link handed over sets the person's ONE password, in every org of theirs. The operator's door
  hands it over always. `POST /v1/signup` refuses `409` an address already invited on this box
  and passwordless: accepting that invitation is what chooses the password.
- **An address is a person's once somebody other than an admin proved it** (migration `0048`:
  `members.verified_at`, `invitations.vouched`; `verified` on every member the doors answer). A
  link handed to an admin in the answer proves nothing about who opens it, so whoever chose an
  address's password first — through their own org's link, or a sign-up — was seated wherever that
  address was invited next, and their invited rows were seated at login. Now a second org seats a
  known person without a link, and `POST /v1/login` seats an invited row, only when some row of
  theirs is verified: they accepted a link that travelled by mail alone, an identity provider (the
  org's, or Google) named them, or the operator invited them (`pinecall-runtime orgs invite`,
  `POST /v1/ops/orgs/{org}/members`). Nobody is grandfathered: an existing person of two orgs is
  invited once more, by mail or by the operator, and that verifies them.
- **A developer's sandbox is not the floor's to open.** Seeing every corner — the agent listing
  by corner, `pinecall-corner`, the processes of every copy — takes `team` AND `app` (an admin's,
  the box's own); a manager opens `team` alone and now sees the org's own corner, as a developer
  does (`auth/keys.py:sees_every_corner`).
- The login doors verify a password against a hash of nobody's when the address has none
  (`auth/passwords.py`), so a stranger's email costs what a member's does and the clock names
  no members. `GET /v1/login/sso?org=` answers one `404` for an unwired org and for none. A voice
  eval no longer reads the voice and language another org's socket declared under the same slug.

### Added
- **A persona says how it is played and when it accepts the call — and a judge reads the call by
  it.** `agent_personas` gains `llm`, `tts`, `voice`, `accepts_when` and `declines_when`
  (migration `0047`). The three knobs are the agent's own words, read by `providers/tuning.py` and
  refused with a 422 at `PUT /v1/personas/{name}` for a vendor or a voice this box does not have;
  `POST /v1/evals/caller` and `/v1/evals/voice` play the caller on that model and read its lines in
  that voice, and unset is what it always was. The rule rides the dispatch (or the chat door) onto
  `call.started`, and a new ring-4 judge, `persona`, reads the finished call against it at hang-up:
  `held` is the caller accepting, `broken` declining. The model playing the caller is never told it.
- **A box may answer to a SECOND name, and its console is the sandbox's.** `PINECALL_SANDBOX_DOMAIN`
  in `box.env`: the same gateway, the same doors and the same bundle, with two things different at
  that name — the page marks itself the sandbox's (`<meta name="pinecall-world">`, which the console
  reads at boot) and **no request that arrives there runs in production**, whoever holds the key
  (`403 … answers the sandbox only`). Caddy serves both names off one `(pinecall)` snippet, and a
  box with one name is what it always was. `infra/box/README.md`, "Two names".
- **The panel an agent draws beside a conversation.** An app may declare a view — one name, on
  `AgentConfig` — and the console then asks the process holding the agent to draw it for one
  conversation at a time, through a dev family of its own: `POST /v1/agents/{slug}/dev/view/view.render`,
  opened by a key with `calls`, because a panel is read where the conversations are read. The
  gateway relays and stores nothing: what the panel holds is the tenant's own data, read in the
  tenant's own process. `docs/protocol/dev-verbs.md`.
- **The box records the ROOM, and whether it records at all is the agent's setting.** A recording
  was written by livekit's own session recorder, which knew two sources and only two: the
  participant the session was pinned to, and its own voice. Everything else a call heard was a
  track of its own and was in no file — **the hold melody**, and **a supervisor who took the
  line**. A box now runs `livekit-egress` (`infra/box/containers/pinecall-egress.container`) and
  every recorded call is one audio room composite of its room, one mix, written to the same
  `recordings/<call>/audio.ogg` the summary has always pointed at. `pinecall-runtime doctor` gains
  an `egress` line, because a recorder that is down is otherwise silent: every call is answered
  and none of them is kept.
- **`record`, a setting of the agent.** Whether its calls keep their audio, per world and per
  corner, versioned like every other knob — one org on a box may record and another may not, and
  neither waits for a deploy. `pinecall agent set --record on|off`.

### Changed
- **A door is a row an operator typed, and a class declares none.** A door was born two ways — a
  row in `routes`, and a field on the tenant's class that travelled with `agent.register` — and
  only the row could be moved, dropped or reassigned. The declared half is gone: `agent.register`
  still carries `routes` so an app on an older package registers, and the gateway reads none of
  it. With it go the door map the registry kept, the precedence between the two tables, and the
  refusals that came out of it (a number claimed by a second socket, a sandbox copy refused
  production's number) — one row per number per org is the table's own primary key now.
  `GET /v1/numbers`, `GET /v1/ops/routes` and `pinecall-runtime routes list` answer rows, with no
  `source` beside them, and `POST /v1/ops/routes` no longer says whose door it took.
- **The widget is not a door any more: every agent is on the web.** A `web` route was a thing only
  a CLASS could declare — the routes table refuses a row with no number, by three separate guards
  — so `POST /v1/tokens` demanded a declaration nobody could type, and a browser could not talk to
  an agent whose class had not said `web = true`. It now asks what the chat socket asks: is
  anybody holding this agent, in my world, in my org. A number is a row somebody bought; a page
  with a tag on it is not, and the two never had to have anything in common. The worker makes the
  widget's route out of the dispatch it already carries (`worker/router.py`), the way the chat
  socket has always minted one.

### Removed
- **`RECORD`.** The box-wide switch is gone: the agent says whether its calls are recorded, so a
  box that had `RECORD=0` keeps recording until each agent is told not to. `PINECALL_RECORDINGS`
  is unchanged — it is where they land.

### Fixed
- **A supervisor's Stop hangs up at once.** The `end` verb reached livekit's `shutdown()`, which
  drains by default: the sentence playing was finished and a reply still being generated was
  generated and spoken first, so a Stop pressed while a slow model was thinking waited the whole
  turn out and was pressed again. `Ending.hangup(at_once=True)` interrupts instead; an app's own
  `call.hangup` still drains, so a goodbye it queued is heard. `session/voice/supervising.py`.
- **A tool runs after the line that announced it, and its receipt is heard before the model
  replies.** The model emits "voy a reservar" and the `book` call in one response, and livekit
  starts the tool under that very line: the booking was made before the caller had heard it would
  be, and the log read `tool.call` before the turn. The tool's own speech cannot be awaited from
  inside it, but the step's words can — `RunContext.wait_for_playout()`, the way livekit's own
  error points at — so the announcement plays out, then the tool runs, then the melody (after its
  grace, and never under the voice). And the confirm read-back is now awaited inside the tool:
  `say()` joins a sentence to the history only once its audio has played, while the reply to a
  tool result is generated the instant the tool returns, so a receipt fired and forgotten was not
  in the history the model answered from and it said the booking a second time, in other words
  ("Reservado: el miércoles…" and then "Su cita queda confirmada para el miércoles…"). One turn,
  one receipt, one reply that continues from it. `session/voice/tools.py`, `reading_back.py`.
- **The agent no longer answers its own questions.** livekit's preemptive generation is off for a
  spoken call as well as a written one. With it on, a caller's end-of-turn landing inside a
  tool's execution window starts a whole new reply before the tool has answered, on a context
  missing it: on one call the agent asked "Is this a house?" and then said, in its own voice and
  under the same speech id, "Yes, a house." — twenty-seven entries before the caller did. Every
  round of that call ran on 4,300 to 4,900 prompt tokens; that one ran on 2,923. It also made a
  second, discarded model call per turn. The cost of turning it off is about half a second of
  latency; the cost of leaving it on was an agent that sometimes played both parts.
- **A spoken persona waits for the answer to the line it just said, not to the one before.** The
  wait between two of a simulated caller's lines could be judged on a snapshot that did not
  contain that line yet: every `agent.state` in it then belonged to the previous turn, and the
  `listening` that ended THAT one read as the end of this one. Requiring the log to have moved at
  all did not catch it — `user.state` entries land while the caller is still being transcribed, so
  the log moves without the transcript arriving. The caller's own newest transcript must now have
  landed after the caller fell silent, which is true of the line just said and of no earlier one.
- **A simulated caller stops when the call is hung up.** The persona is played inside the gateway,
  by a loop that reads the call's own log every turn; the call itself belongs to the worker, so
  ending it — a supervisor's verb, the app, the agent — left the caller improvising into a room
  nobody was in, one thirty-second wait per turn it had left. It now reads `call.ended` off that
  same log and stops there, and the wait between two lines gives up at once on a call that is over.

### Changed
- **Every base an agent reads is searched in ONE pass, ranked against the others.** A turn asked
  each attached base its own query and merged the answers afterwards — and a fused score is read
  relative to the best of ITS query, so every collection answered 1.0 for its own best chunk
  whatever it was about: three attached bases took three of a turn's four slots before the ranking
  had said a word. Now one query, both branches over the union, one fusion, and then each chunk
  against the floor of its own attachment (`--min-score` is the attachment's; `k` is the turn's).
  The candidate pool grows with the bases asked, so a small collection is not crowded out by a big
  one. `docs.sources` now carries the `base` each chunk came from.
- `docs/the-runtime-cli.md` is the verbs; the environment table and the two walkthroughs are
  `docs/the-environment.md`, a page of their own (and a page of the site).

### Added
- **An agent may say how sure its ears have to be that the caller has finished.**
  `turn.eot_threshold` and `turn.eager_eot_threshold`, handed to Deepgram Flux, which is the one
  recogniser here that calls the end of a turn itself. The endpointing we already had is a clock,
  and a clock only catches the silences Flux is unsure about: a caller it is CONFIDENTLY wrong
  about is cut whatever the timeout says, and Deepgram measures that at as much as a fifth of the
  turns at its own default of 0.7. The eager bar is the lower confidence at which it says the turn
  MIGHT be over, which is what livekit's speculative generation hangs off — so the first can be
  raised without paying for it in latency. A declaration whose eager bar sits above its real one
  is refused where the class is read, because Deepgram refuses the socket and a refused socket is
  a call with no ears at all.

### Fixed
- **The hold melody no longer comes up under the agent's own voice.** It waited 0.6 s after a tool
  began, and an agent that announces what it is about to do starts the tool in the same breath —
  so the caller heard the melody and the announcement at once. It waits 2.5 s now, by which time
  the line has been said and the silence is real.
- **A spoken persona no longer talks over the agent.** The wait between two of a simulated
  caller's lines read the log as it stood a moment ago, so a `listening` left over from the
  PREVIOUS turn was accepted as the answer to the line just said: the caller spoke again a second
  before the agent had finished, and the agent's turn came back cut to two words. The snapshot now
  has to have moved on since the caller fell silent, and the agent has to have LEFT `listening` —
  which is what it does the moment the line is handed to it, and the one thing a transcript split
  into sentences by Flux cannot fake.
- **A file's front matter is not a chunk.** Every static-site generator and every scraper opens a
  `.md` with a fenced block of metadata — `source:`, `title:`, `scraped_at:` — and it was the
  file's first section: embedded, indexed and retrievable. On a real scraped site that was **75 of
  537 chunks, one in seven**, and one of them came back as the evidence for a caller's phone
  number. Push a base again to rebuild it without them.
- **Neither lookup runs on a turn that could not be a query.** A turn with no letter in it is a
  number being read out — a phone, an order, a card — and `305 555 0101.` searched a cleaning
  company's base and came back with its data-center pages. No prose index answers one, the
  contact's facts would rank by nothing, and a caller's digits are the last thing to send to an
  embedder. The eager path had a floor of its own (four words); the turn-end path had none.
- **`doctor`'s embedder line embeds a word.** It used to GET the provider's base URL, and a vendor
  answers the same status there for a live key, an expired one and none at all — so a box with a
  dead `PERPLEXITY_API_KEY` read `✓ embedder … HTTP 404` while every lookup on it was skipped. The
  check now asks the embedder this box is configured with for one vector, with the real key, and
  fails when it does not answer or answers at a width the `halfvec` columns were not declared at.
- **The `lk` line tells THIS machine how to install it.** It recommended `brew install livekit-cli`
  on a Debian box, where brew is not installed and nothing else was; on anything but a Mac it names
  the script LiveKit publishes.
- **`fleet list` no longer totals `0 seats free` for a fleet nobody counted.** A worker with no
  `PINECALL_MAX_JOBS` is gated by its CPU and reports no seats, which read as a full fleet beside
  the same line saying it accepts calls. Those totals say `seats gated by cpu, uncounted`.
- **`fleet uncordon` says whether there is still a worker there.** A cordon is how a machine is
  retired: the worker drains, exits 3, and the unit is written not to bring it back. Lifting the
  cordon afterwards printed `uncordoned` and left the box with **no worker**, while `fleet list`
  went on saying `accepting` for the thirty seconds a heartbeat counts for. It now says which it
  did — takes calls again, or already drained and left, with the line that starts it — and
  `cordon` says out loud that the worker does not come back on its own.
- **`migrate up` names only the post-deployment files this database has not run.** It listed every
  `.post.sql` on disk as still waiting, applied or not, and sent a person to `migrate up --post`
  for one they had applied weeks ago. It had the table's answer two lines above it.
- **`migrate status` asks the database about post-deployment files too.** Every `.post.sql` read
  `waiting` off the disk alone, so one a person had already applied still looked pending for ever.
  Each file is now `applied`, `behind` (a startup file this database has not run) or `waiting`, by
  what the table says, and what waits is counted at the end.
- **Nothing this runtime refuses reaches a terminal as a traceback.** The dispatcher prints any
  refusal the runtime raises deliberately as one sentence on stderr and exits 1. The one that made
  it necessary: a `.env` that is there and cannot be opened — `sudo -u pinecall <verb>` inside
  another user's home — came out of the middle of python-dotenv as a `PermissionError`. It reads
  `cannot read .env: Permission denied — a .env that is there is never skipped in silence`.
- **A database that does not answer is a sentence, and never the password.** Every refusal that
  names the database went through the raw DSN, so `migrate status` against a box whose password had
  changed printed `postgresql://pinecall:<the password>@…` under an asyncpg traceback. The store
  strips it once (`without_password`), the doctor and the test suite read the same function, and
  `migrate` catches the refusal instead of raising it.
- **`migrate` with no verb READS.** A bare `migrate` meant `migrate up`: somebody typing it to see
  what it would do migrated the database. It is `status` now, and applying is typed in full.
- **`pinecall-runtime box` with no verb exits 0**, as every other group's bare form does; it exited
  2, so listing the box's verbs looked like a command that had failed.
- **`WS /v1/chat` carries every entry of the call, as the door has always claimed.** A watcher of
  a text call was fed from the session's own `emit`, so the entries the GATEWAY writes on the same
  log — `docs.sources` and `memory.ops`, from `lookups/service.py` — reached the caller's socket
  and the WhatsApp thread never: a reader following the seqs saw one skip and the stored log had
  it. A log takes more than one tap now, and a watcher is one: inline, in order, before the append
  returns, so the WhatsApp door still answers only once the contact has the message.
- **A production key is never told the line is its own.** Production has no corners: `held_by`
  answers None for every key there and so does the line's holder, so `holder == whose` was
  `None == None` and `pinecall line` told a laptop holding nothing that the number "rings in this
  terminal", about a box.
- **A key in `?token=` is refused.** The one door that reads a bearer out of a URL — the log's SSE
  and JSON flavours, because an `EventSource` cannot set a header — took an API KEY there as well
  as a room token, against what its own paragraph has said since it was written. A URL is written
  down: the access log, the referrer, the history of whatever followed it, and a key is the whole
  tenant until somebody revokes it. Only a short-lived room token travels in the query string now;
  the header is untouched. Found by exercising the door against production.
- **`GET /v1/evals/runs?limit=` counts YOUR runs.** The cut came before the org filter, so the box's
  newest N were read whatever tenant they belonged to and what survived was whatever share happened
  to be yours: on a box where two other tenants had run last, `?limit=2` answered an empty list
  (`pinecall runs list --limit 2`, production). The page is filled with the org's own now, reading
  ahead until it has the limit or the table is spent.
- **The console owns `/docs` again.** FastAPI mounts its Swagger there by default and a route wins
  over the catch-all that serves the page, so `/docs` — the console's screen for the org's
  knowledge bases — opened the API reference instead: a pasted link landed on Swagger, and a reload
  threw a person out of the console. The interactive schema is `/v1/docs`, with every other door;
  `/openapi.json` stays where a generator looks for it.
- **The supervisor's desk works from the console.** Every verb a desk sent against a production
  call was refused — `403 that call's agent belongs to another org` — because the door asked the
  LIVE registry whose the call was, in the world of the key that asked. A person's key is minted
  into the sandbox whatever world the page reads in (`api/login.py`), so the lookup missed and a
  key that owned the call was told it was somebody else's. Whose a call is, is what its LOG says:
  the one question every read door already asks (`api/calls/sink.py`), now asked here too, by
  `POST /v1/calls/{call}/verbs` and `WS /v1/attach` alike. The sentence is `403 that call belongs
  to another org`.

### Removed
- **The operator's page, `/admin`.** The gateway served a second bundle there, opened with the
  box's ops key typed into a browser, doing a poorer version of what the console's **Box** group
  already does for a person the box made an operator — Organizations, Fleet, Routes, Box usage,
  Box settings — with that person's own key. One page, one build, one credential. The `/v1/ops/*`
  doors are untouched, and a box's first org is made where it always was:
  `pinecall-runtime init --org … --email … --person …`, now written down in the README.

### Changed
- **A persona belongs to the org, not to one agent.** A caller is a person on the phone, and who
  they are does not depend on which of the org's agents answers — so `agent_personas` is keyed
  `(org, name)` and the doors lost the agent in their path: `GET /v1/personas`, `PUT` and `DELETE`
  on `/v1/personas/{name}`, same `evals` scope, same 422/404/409. The per-agent doors are gone
  rather than kept beside them; this is pre-1.0 and one shape beats two. Migration 0045 merges a
  name two agents of one org both held by keeping the most-recently-written under that name and
  the other under `<name>-<agent>`, so nothing is lost and a person can delete it from the
  console. Production had no such collision when this was written.

### Added
- **A text call has no line to transfer.** Five of the six supervise verbs apply to a conversation
  with no room — the widget's chat, WhatsApp — and `transfer` is refused with
  `409 supervisor.verb: a text call has no line to transfer`; the console's desk draws neither the
  button, nor the ear, nor a microphone for one.
  [docs/protocol/gateway-api.md](docs/protocol/gateway-api.md)
- **What a persona has done.** `GET /v1/personas/{name}/runs?limit=&before=` answers every
  simulation that caller has run in the key's world and corner, newest first: the call id, the
  agent, when, how many turns the caller took, how it ended, its outcome line, what it cost and
  how the judges answered — paged exactly as the sessions list is. It is read off the call index
  and never by folding a log.
- **A simulated call says who is being played.** Nothing recorded the persona on a call before,
  so the Personas screen could show what a caller is and nothing about what it has done. The name
  now rides the call's own `call.started`, beside `run`: a written simulation puts it on the chat
  socket (`/v1/chat?persona=`) and a spoken one on the dispatch, because there the worker is what
  writes that entry. `log/facts.py` projects it into `call_facts.persona` (migration 0046) the way
  every other fact is projected. There is no backfill — the name is nowhere in the logs of the
  calls that already happened — so every older simulation reads as "no persona", which is what it
  honestly is.
- **The personas are the gateway's.** `GET /v1/personas`, `PUT` and `DELETE` on one
  by name, opened by `evals`: a synthetic caller is a goal, a manner and a few facts — the same
  kind of thing as the voice and the lexicon, and no more a file of a project than those are. One
  list per org, not per world: a caller is a test, not something a customer hears. Migration
  0042, and 0045 which took the agent out of the key.
- **The hold melody's doors are in the protocol.** The six of them — `GET`·`PUT`
  `/v1/agents/{slug}/pipeline/hold-audio`, `…/audio`, `…/played`, and the worker's
  `GET /v1/agents/{slug}/hold-audio[/audio]` — were in no public page.
  [docs/protocol/pipeline-api.md](docs/protocol/pipeline-api.md) documents them whole, and
  every-door.md lists each.

### Fixed
- **An agent's settings fall through corner by corner, knob by knob.** Resolution took the whole
  row of the first corner that had one, so `agent set --voice x` wrote a row with only a voice in
  it and the agent stopped reading the team's stt, llm, memory, knowledge and bases — they went
  silently unset — and `clear` left an empty row that won and blanked everything under it. Every
  knob now falls through on its own: a corner supplies the knobs it actually set and the corner
  below supplies the rest, a knob set to a falsy value (`turn {endpointing_ms: 0}`) is set and
  wins, and an empty row supplies nothing and is invisible to resolution. One definition,
  `orgs/resolving.py:resolved`, which both stores read through; the versioned writes and the
  per-corner doors are untouched. [docs/protocol/settings-api.md](docs/protocol/settings-api.md)
- **`bases: []` is a corner saying it reads no base.** It was the one knob with no absent form: an
  empty list was dropped on its way into the column, so "take the team's bases off this agent" was
  written as the same row as "nobody ever attached one" and both fell through to the corner below.
  `Tuning.bases` is absent as `None` now, like every other knob, and an explicit `[]` is stored and
  wins over the corner below. **This changes what `bases: []` means on the wire** — it used to mean
  nothing and now means no base, and do not inherit one; leaving the field out is what falls
  through. No migration: the column is `jsonb` and no row can hold an empty `bases`, because one
  was never written. A `words` key sending `bases: []` is refused by name, as it is for any other
  move of the pipeline. [docs/protocol/settings-api.md](docs/protocol/settings-api.md)
- **Renaming a persona is one statement.** It was an INSERT and then a DELETE, so anything that
  cut between them — a process stopped, a connection lost — left the agent holding both names.
  The two are one `WITH gone AS (DELETE …) INSERT …` now: the old name goes and the new one
  arrives together, or neither does. The refusals are untouched (`404` nobody wrote that name,
  `409` somebody else holds the new one).
- **The personas belong to an org, and the table says so.** `agent_personas` was the one per-org
  table with no `REFERENCES orgs (id) ON DELETE CASCADE`, so deleting an org left its synthetic
  callers behind. Migration 0043 adds the constraint `NOT VALID` — enforced for every write from
  the moment it runs — and `0044_personas_org_fk_validated.post.sql` checks the rows that were
  already there: a person runs it when they choose, `pinecall-runtime migrate up --post`.
- **A spoken caller waits for the greeting.** A simulated caller (and a spoken golden) said its
  first line the moment the agent joined, over the greeting. `every_turn` now waits for the
  opening — the agent's first `turn.agent` and `listening` after it, or an agent that has only
  listened for three seconds — before the first line, fifteen seconds at most.
- **A server's token opens `evals`.** The console asks the process holding an agent to run a
  simulation or a suite, and that process knocks at the evals doors on its own token: without the
  scope, Simulations on the production console was refused `403 this key does not open evals`.
  Tokens made from now on carry it; an app minted before is minted again (`make key`).
- **A simulated caller speaks like a person, in the agent's language, never in its voice.** The
  caller's lines were read by the box's speech tool in a Spanish voice (`say -v Mónica`,
  `espeak-ng -v es`): an English agent's ears heard Spanish nonsense from the first turn, and even
  in the right language the robot's name came back as another name. The caller now speaks with
  ElevenLabs through the same vendor file the agent uses, in a premade voice no agent is given
  (Brian, or Jessica when the agent already is Brian), in the language of the config the agent
  runs on; the television of a noisy line is a third voice. `espeak-ng` leaves the box's packages,
  and the gateway needs `ELEVEN_API_KEY` for a spoken simulation.

### Added
- **A base keeps its files, and they are read and changed one at a time.** A base was chunks
  alone, so nothing of it could be seen or edited without the folder on a laptop and a push of the
  whole. Migration 0041 keeps every file as pushed, and four doors work on one file:
  `GET /v1/knowledge/{base}` lists them, `GET …/files/{path}` reads one, `PUT …/files/{path}`
  puts one — new, or replaced in place, re-cut into its own chunks — and `DELETE …/files/{path}`
  takes one out, the base with it when it was the last. The console's Docs opens a base onto
  its files: add one from disk, write one, edit one, take one out. A base pushed before this
  lists no files and says `kept: false` until it is pushed again.
- **The models each vendor runs, on the wire.** `GET /v1/providers` and the pipeline report carry
  `models`, keyed `<modality>/<vendor>`, the vendor's default first — what each tuned vendor file
  registers — and the report also carries `defaults`. The console's Settings picks a model from
  that list instead of a box a model name is typed into. Deepgram lists the two Flux models its v2
  socket speaks, never nova.
- **Which processes hold an org's agents, where, and a stop.** `GET /v1/apps` lists every app
  socket in the request's world — its agents, the machine (`host`, now on `agent.register`), the
  address, the SDK, whose corner, since when — and `POST /v1/apps/{app}/stop` tells one it was
  stopped (`error` code `stopped`) and closes it; the SDK exits instead of reconnecting.
- **Production access is a switch on the person.** `members.production` (migration 0039, off for
  everybody: an admin opens production by the role): the role says what somebody does, the switch —
  set by an admin at `POST`/`PATCH
  /v1/members` and the operator's invite — whether they may do it in production. An admin always
  may, and `production: false` on one is `409`. It is read at every request, so taking it away
  closes the next one; member JSON and `GET /v1/whoami` carry `production`.
- **An agent's settings are the org's, per world, per corner, a version a row.** What an agent
  runs on — vendors, models, the opening, the cut of a turn, what is remembered, the bases — and
  the org's words are set at `GET`/`PUT /v1/agents/{slug}/settings` and `/v1/lexicon`, with
  `history`, `diff` and `rollback` beside them, kept in `agent_config` and `lexicon`
  (migration 0037) and laid over the class at the one place every session is built. A corner reads
  its own newest, else the org's own, as knowledge falls back; the whole set is written with the
  version it was read at and a corner that moved answers 409; a request in production writes
  production's org's-own corner directly; and a call's head row keeps the two versions it ran on
  (`GET /v1/calls/{call}/settings`). `docs/protocol/settings-api.md`.
- **The `words` scope: the floor fixes what the agent says.** A supervisor's and a manager's keys
  set the opening's words, the lexicon, what is remembered and what the agent knows by heart, and
  are refused a vendor by name.
  0037 hands it to every key that holds `supervise`; `developer` carries it too.
- **What the agent knows by heart is a setting: `knowledge`.** The business as the org describes
  it, in Markdown, one field of the agent's settings — set by whoever holds `words`, from the
  console or `pinecall agent knowledge edit`, versioned like the rest — and read whole into the
  static knowledge block of every call. It is not the RAG: the bases a turn searches are `bases`
  in the same settings (`[{base, mode, k, min_score}]`; migration 0040 renames the key 0037 kept
  them under), a turn's search fans out over every one attached, and `GET /v1/knowledge/attached`
  says which agents read each base. `agent.configure` refuses a class that searches for itself
  (`uses_knowledge`) in a world that attaches it no base, and one whose world attaches a base
  never pushed there, each naming the `pinecall docs` verb that fixes it.

### Changed
- **The class is code; the world is environment.** `agent.configure` reads only the contract —
  the prompt's layout, the language, the tools, `uses_knowledge`, the visibilities, the events. A
  voice, the models, an opening, a hangup, the cut of a turn, `says`, `hears`, `memory`, `docs` and
  a `knowledge` file still travel from an app on an older package and are ignored: the world's
  settings are the one source, and a world with nothing set runs on the runtime's defaults. The
  seed a class used to give a world's first version is gone with it. The wire fields stay one
  release and are removed in the next.
- **`pipeline_overrides` is dropped (0040), and `PUT …/pipeline/overrides` with it.** 0037 stopped
  reading the table; the six-knob door wrote through the settings store for one release. The
  Pipeline door reads what the next session would run on and turns nothing: the settings do.
- **A tenant's app on the box runs `pinecall start --prod` on `PINECALL_KEY`.** `pinecall-app@<name>`
  exports the server's token off its `.key` credential and the gateway on loopback as
  `PINECALL_URL`, with no `pinecall login` first; the app installs `pinecall` 0.5.0 or later.
- **One key per person; the request names the world.** Every key minted for a person — invitation,
  login, SSO or Google code, sign-up, terminal pairing, another org — carries the role's scopes
  whole, `app` included, and is stored in the sandbox; migration 0039 moves every existing person's
  key there. Each request says `pinecall-env: sandbox|production` (none is the sandbox), on every
  door and both sockets; production answers only with production access (`403 <name> has no
  production access`). A server's token keeps its one world, and a header asking the other is
  `403`. An operator's visiting key stays production's.
- **`/v1/keys` are the org's tokens.** `POST /v1/keys {label, env}` makes a server's token — only on
  a person's key that opens `app`, production's only with production access — with the fixed scopes
  `app` · `calls` · `talk` · `knowledge`, recording `created_by`, and it outlives its maker.
  `GET /v1/keys` (any key) lists every server's token and the asker's own keys (every person's with
  `keys`), each with `kind`, `env`, `created_by` and `last_used_at` (0039, written when a key opens
  the app socket or asks `/v1/whoami`); revoking takes your own keys, the tokens you made, or any
  with `keys`. New keys say whose in the prefix — `pc_` a person's, `pc_live_`/`pc_test_` a
  server's — and `pk_` keys still verify.
- **Production's settings, lexicon and base are set directly.** `PUT /v1/agents/{slug}/settings`,
  `PUT /v1/lexicon` and `PUT /v1/knowledge/{base}` write the world of the request; in production the
  org's own corner, by a key with production access or a server's token in its release step. The
  goldens run in CI before a deploy.
- **`agent.configure` refuses a base never pushed.** A class whose settings or `docs` read a base
  nobody pushed in that world is refused where it declares itself, naming `pinecall knowledge push`,
  and not in a call whose turns would find nothing.
- **The app may search the base itself.** `POST /v1/calls/{call}/lookup` also answers the app
  (`app`) for `this.knowledge.search`, and a search's answer is the wire's `SearchFound {chunks:
  [{path, heading, text}]}`.
- **`pipeline_overrides` is absorbed.** Its rows became version 1 of both worlds of `agent_config`;
  `PUT /v1/agents/{slug}/pipeline/overrides` stays one release for the console's Pipeline screen,
  writing a version of the same store, and the table is read by nothing. The gateway no longer
  caches what an operator turned: it reads the corner's tuning per session.
- **`supervisor` and `manager` open `memory`.** What the agent remembers about the caller is what a
  person beside a live call, or running the floor, has to see; the two presets now carry the scope,
  as `developer` and `admin` already did. A preset is what the NEXT key minted opens.
- **`GET /v1/memory`: what every agent of the org has learnt, on one page.** The agent's memory
  door without the agent — the current facts across every agent and contact, newest first, each
  with the `agent` whose call taught it; the same `q`, `after` and `limit`, on the `memory` scope.
- **A hold melody while a tool runs, on the phone and on the web.** The worker publishes a
  second audio track with livekit's `BackgroundAudioPlayer` and plays it around each tool's round
  trip: after 0.6 s, looped at 60 % with a fade, once for tools side by side, stopped before the
  confirm read-back. Every agent plays "A New Life" (session/a-new-life.ogg, made from the first
  Pinecall's mp3 — its 8 kHz wavs played an octave high on a 16 kHz track). The pipeline has doors
  of its own for it: `GET`/`PUT /v1/agents/{slug}/pipeline/hold-audio` (the body is the file; any
  format PyAV decodes, converted once to Opus 48 kHz mono, 20 MB and five minutes at most),
  `…/audio`, and `…/played` (`default` or `off`). Kept in `hold_audio` (migration 0036); the worker
  fetches a clip by its hash once per box. `docs/protocol/pipeline-api.md`.
- **"Continue with Google", box-wide, configured by the operator.** `GET /v1/ops/signin` lists
  every box-wide provider (`{google: {configured, client_id, redirect_uri}}`);
  `PUT /v1/ops/signin/google {client_id, client_secret}` keeps one OAuth client at Google for
  every org's people, its secret under the vault key, Google's discovery checked before anything
  is kept; `DELETE` forgets it. `GET /v1/login/google` sends a person to Google
  (`openid email profile`, PKCE, state, nonce — the SSO's own code) and the callback matches the
  **verified** address against every org's members: an active member lands in the oldest org of
  theirs a password would open, a member still invited is seated by it, nobody is sent back
  with `/?refused=<why>`. An org whose own SSO is `required` is not entered this way.
  `GET /.well-known/pinecall` gains `google`. A second box-wide provider is a row.
- **The box's mail and the letters' brand, configured by the operator** (migration 0035,
  `box_settings`: one row a setting, its secret under the vault key). `GET`/`PUT`/`DELETE
  /v1/ops/mail` and `POST /v1/ops/mail/test` store the mail server the box posts through, the
  same body as an org's and the password sealed the same way; a stored mailbox wins over
  `PINECALL_SMTP_URL`, an org's own still wins over both, and the envelope says `source`. The
  doctor's mail line says which one it read. `GET`/`PUT /v1/ops/brand` is `{name, logo_url,
  accent}` — Pinecall, no logo, `#5b3df5` until set; a field left out keeps, an empty one
  resets — and the letters carry it: the logo at 28px above the card (the one outside
  resource a letter may ever fetch), the name and the accent everywhere they said Pinecall.
  `GET /.well-known/pinecall` gains `brand`. `docs/protocol/the-box.md`.
- **An operator of the box sees every org from the console's switch.** `GET /v1/login/orgs` rows
  gain `member`; for a person the box made an operator the list is every org there is, `member:
  false` and `role: "operator"` where they are none. `POST /v1/login/org` lets them into any org
  on a production key with the admin role's scopes, labelled `operator · <email>`, whose
  `subject` is `operator:<email>` — no member row, no seat, attributable by address wherever a
  subject is written down. `GET /v1/whoami` gains `operator` and `visiting`. Such a key is asked
  about on every verify, so revoking the flag, disabling or removing the person stops it on the
  next request; it opens no sandbox and pairs no terminal. **Changed with it:** the operator
  flag is the PERSON's — a key of theirs in any org of theirs opens `/v1/ops/*`, not only the
  key of the org whose row carries the flag.
- **A member can be removed for good.** `DELETE /v1/members/{id}` (`team`) answers `204`: every
  key of theirs is revoked first, then the row goes with its open invitation and reset links, and
  the seat is free. `409` in a sentence for removing yourself and for the org's last active
  admin; `404` for an id that is not this org's. The log keeps naming the id as text. The
  operator's twin is `DELETE /v1/ops/orgs/{org}/members/{id}` (the same rules less "yourself")
  and `pinecall-runtime orgs remove-member <org> <email>`.
- **Outbound email over generic SMTP** (migration 0034). A box posts letters through
  `PINECALL_SMTP_URL` (`smtp://user:pass@host:587` STARTTLS, `smtps://…:465` implicit TLS — SES,
  Postmark, Mailgun or a server of one's own; a systemd credential) as `PINECALL_MAIL_FROM`. An
  invitation (`POST /v1/members`, and the operator's) and an admin's reset
  (`POST /v1/members/{id}/reset`) are mailed to the person; both answers keep the token and gain
  `mailed`, which says a letter was handed over, never that it arrived. **A forgotten password
  is self-service**: `POST /v1/login/reset {email}` answers `202` whoever asks, shares the
  login's throttle, and mints a one-use link only where a letter can carry it and the org does
  not sign in with its provider. An org may wire its own account at `GET`/`PUT`/`DELETE
  /v1/org/mail` (`team`), its password sealed under the vault key, which wins over the box's;
  `POST /v1/org/mail/test` sends one and waits. Every send happens after the door answered, and
  what came of it is on the org's row (`verified_at`, `last_error`) or in the box's log. With no
  mail anywhere every door answers exactly as before. `GET /.well-known/pinecall` gains `mail`;
  `pinecall-runtime doctor` gains a mail line and `--mail-to <address>` (`make doctor MAIL_TO=…`).
  The letters are one branded, table-based, inline-styled frame with a plain-text twin, and
  fetch nothing: no image, no webfont, no pixel.
- **Single sign-on, one OpenID Connect provider per org** (migration 0030). An admin wires it at
  `GET`/`PUT`/`DELETE /v1/org/sso` (`team`): the issuer, the client, the email domains it admits,
  the role an address nobody invited is seated with — none by default — and whether a password
  opens the org at all. The client secret is sealed under `PINECALL_VAULT_KEY` as a provider key
  is, and no door reads one back. A person signs in at `GET /v1/login/sso?org=…` (authorization
  code, PKCE, state and nonce, one use and ten minutes) and lands on `/?login=<code>`, the login
  code the console already spends, so no key is ever in a URL; `POST /v1/login/sso/discover`
  tells a sign-in page which orgs a domain signs in with, saying nothing about who exists. The
  terminal pairing is untouched. The break-glass is the box's: `pinecall-runtime orgs sso <org>
  --off`, over `PUT /v1/ops/orgs/{org}/sso/required`.
- **Calling somebody back.** `POST /v1/agents/{slug}/dial {to, from?}` (`talk`) answers `202` with
  the call it became: the guards, then `call.dialing` carrying both numbers and who asked, then a
  worker dispatched into a room named by the call, which places the leg itself and writes `busy`,
  `no_answer` or `dial_failed` when the far end never picked up. `GET`/`POST /v1/carrier/outbound`
  (`numbers`, `?dry_run=true` for the plan) is the trunk it dials THROUGH — Twilio's termination
  label and a credential list minted once on the tenant's account, or the SIP peer the tenant
  declared with the new optional `outbound_host`, `outbound_transport`, `outbound_username` and
  `outbound_password` on `PUT /v1/carrier` — and then one LiveKit outbound trunk per org.
  Migrations 0031–0033. The protocol's `call.dial` command stays unanswered on the app socket and
  now says so by name rather than as `no_session`: placing a call is `talk`'s door, not `app`'s.
- **What an org may dial, and only an operator sets it.** `PUT /v1/ops/orgs/{org}/dialling`
  (`pinecall-runtime orgs dialling`) replaces the whole set — `dial_anywhere` off, six dials a
  minute, two hundred a day, ten minutes a call — and a guard left out goes back to the code's default and never
  to "no limit". Every dial asked for is written to the `dials` ledger, taken **or** refused with
  the guard's one word, because a burst of refusals is the shape of an attack. Satellite and
  global-service ranges are never dialled at all. `dialling` rides on `GET /v1/ops/orgs/{org}`.
- **The widget's settings, kept by the gateway.** `GET`/`PUT /v1/agents/{slug}/widget` reads and
  replaces `{title, tagline, greeting, accent, autostart}` per org, world and agent (migration
  0029): what a console sets and writes into the snippet it copies. Read with `talk`, set with
  `pipeline`.
- **Signing in, before a key.** `POST /v1/login/orgs {email, password}` says which orgs a person
  may sign in to and mints nothing, throttled like the login. A forgotten password is handed back
  by an admin: `POST /v1/members/{id}/reset` (`team`) answers a one-use link that sets a new one
  at `POST /v1/invitations/{token}`. The box sends no email. A link never re-activates a member
  who was disabled after it was issued.
- **The inbox: threads by contact.** `GET /v1/agents/{slug}/threads` lists an agent's contacts with
  their last message and what the reader has not read; `…/threads/{contact}` merges a contact's
  calls into one thread; `…/read` moves the person's own read cursor; `…/messages` says something
  as the agent on the contact's open WhatsApp conversation, within Meta's 24-hour window.
- **Memory across callers.** `GET /v1/agents/{slug}/memory` lists the current facts an agent's
  calls taught, every contact, newest first, filtered and paged; `DELETE /v1/memory/facts/{id}`
  ends one wrong fact the bi-temporal way — the row stays, superseded from now. `memory`.
- **`POST /v1/evals/judge/{call}`: a finished call judged on ask.** The hang-up's judges over a
  call nobody judged — its org had judging off, or the judge broke — or again with `?again=true`;
  the `call.score` lands on the call's own sealed log, the one entry a sealed log takes. `evals`.
- **`GET /v1/insights?day=`: a day at a glance.** Conversations today and yesterday, the share of
  finished calls no person took part in, the median e2e_latency, the spend, the three doors, every
  call and the live ones, each agent's day and held-rate, and the month's budget beside what was
  spent — three reads of the call index, the day cut in UTC. `calls`.
- **An org may turn judging off.** `GET /v1/org/judging` (`calls`) says whether the org's calls
  are judged at hang-up and the box's ceiling; `PUT /v1/org/judging {on}` (`usage`) turns it. Off,
  a call seals with a `call.score` that carries no verdict and says why; the worker asks
  `GET /v1/calls/{call}/judging` before it judges a spoken call. Migration 0027.
- **A monthly budget beside the quotas.** `budget_eur` (whole euros, both worlds) is set with
  `PUT /v1/ops/orgs/{org}/quotas` and `orgs quota --budget-eur`; nothing is refused over it.
  Migration 0028.
- **The session lists filter, count and page, and each row says how it was judged.** `GET
  /v1/sessions` and `GET /v1/agents/{slug}/sessions` take `q` (call id, a number's digits, the
  caller's name, the outcome), `agent`, `channel` and `before`, and answer `total` and `next`
  beside the rows; every row carries `score` and `flags` (`escalated`, `low_score`, `promise`).
  `docs/protocol/console-api.md`.
- **A `promises` judge.** A call where the agent committed the business to a call back, a visit,
  a price or a follow-up that no tool call records answers `broken`. Code finds the phrases; the
  judge model is asked only when there are some, under the same ceiling as every judge.
- **The call index.** Every append folds what it says into one row per call (`call_facts`,
  migration 0025): the door, both numbers, the contact, how it ended and what it cost, how the
  judges answered, whether a person took part, every turn's e2e_latency. The console's list, its
  day and its inbox read that row instead of reducing every log. Calls from before the migration
  are folded by `pinecall-runtime migrate up --post` (0026), which also builds the corner index.
- **A `chat` visit waits for no seat.** The worker waited five seconds for a browser holding a
  `talk` token before every greeting, and a chat token is not one: measured on the live line as
  `seat: 5.0` with every other step of the start-up inside half a second. The wait is skipped for
  a written visit, as it is for WhatsApp, and the live line now carries the breakdown of every step.
- **A `chat` visit is a written call.** The worker ignored the scope the token door wrote into
  the dispatch and ran the spoken session for every web visit: a chat page read the agent's words
  at the pace a voice nobody heard was saying them, two seconds behind and billed as speech, and
  the greeting waited on a synthesis nobody heard (box, 2026-09-16, the first chat from a tenant's
  page). A `chat` scope now gets the written session — no ears, no voice — and a room with audio
  off both ways; the words reach the page as the model writes them.
- **A chat token hears the room.** It was minted audio off both ways, and LiveKit hands text
  streams to subscribers only: a page typed into the room on `lk.chat`, the agent answered — the
  call's log had the turn — and the page never saw a word (box, 2026-09-16). `chat` publishes
  nothing and subscribes now; the page is where no audio is attached.
- **`end_call` is in the log like any other tool.** The app's tools are written as `tool.call`
  and `tool.result` on their way through the gateway; livekit's own `end_call` never passes there,
  and a log without it showed the agent speaking twice in a row with nothing in between — "it
  talked to itself" (box, 2026-09-16, a Talk from the console). The hang-up callback writes the
  pair now, with the call id and the speech it ran in.
- **Nothing is said after `end_call`.** livekit's tool answers the model "say goodbye to the
  user" and lets it generate one more reply once the call is already ending — Haiku, told that,
  said "I understand. I'm ready to help the next caller" to a caller it had just thanked and
  wished goodbye (box, 2026-09-16, a Talk from the console), and the log showed two agent turns in
  one speech. The tool now tells the model to say its goodbye in the turn it hangs up in, and asks
  for silence after it (`StopResponse`, livekit's own way); the session closes when that turn's
  speech is played out.
- **One worker serves every org's voice: the worker is the box's, not a tenant's.** The box's
  worker held ONE org key (org `default`) and every door it knocked — `GET /v1/routes`, the config
  and provider-key doors, `POST /v1/calls`, the heartbeat — resolved by that key's org and world,
  so a spoken call reached org `default` in production and nobody else: `POST /v1/tokens` minted
  for any org, the job arrived, and the worker died with `NoRoute` (box.pinecall.io, 2026-09-15).
  Now the worker holds a key with the new **`fleet` scope** (`keys issue --scope fleet`, what
  `pinecall-worker-key.service` mints; in no role's preset), the **dispatch names whose call it
  is** — `org`, `env` and, in the sandbox, the `holder` — written by the token door from the
  minting key and by a tenant's SIP rule, and the worker's doors resolve by the call: the fleet
  asks `?org=&env=&holder=`, or `?number=&channel=` for a call on the box's own trunk, and the
  gateway answers that org's. A tenant's key opens exactly what it did: naming another corner is
  403 in keys.md's words. **A box born before this re-mints its worker key** — see
  `infra/box/README.md`, "Where the keys come from".
- **A tenant's app can be held on the box: `pinecall-app@<name>.service`.** The docs said an app
  runs on a server of the tenant's, and a tenant with none ran it on a laptop — which is what
  answered the phone until the laptop slept. The manifest now installs a template unit, one
  instance per app, that signs in with `pinecall login --key-stdin` off a credential of its own
  (`pinecall-app-<name>.key`), sources the app's `.env` from another (`pinecall-app-<name>.env`,
  `set -a` in the unit's own shell — `EnvironmentFile=` cannot read a credential) and runs `pinecall run --env production` against the gateway on
  loopback. The box installs NodeSource's Node 24 and pnpm for it, from cloud-init and from the
  manifest alike. docs/a-box-in-production.md §7 has the three `make` targets an app's deploy has.
- **A simulated call says whose it is.** `pinecall simulate --voice` and every spoken golden
  dispatched the worker naming only the agent, so the worker looked for its routes in its own
  org — the box's, which holds nobody's agents — and every spoken call outside org `default` died
  with `NoRoute` before a word was said. The dispatch carries `org`, `env` and the sandbox
  `holder` now, the same three `POST /v1/tokens` writes, taken from the key that asked for the
  call and from the registration the run is against.
- **`keys issue --scope fleet` mints the scope the box's own unit types.** `--scope` chose from
  the scopes a tenant's key holds, which by design exclude `fleet`, so the one verb meant to mint
  it refused it and a box that rotated its worker key came back with no worker key at all.
- **0.1.0, and `pinecall-protocol` travels as a range.** The wheel PyPI serves now declares
  `pinecall-protocol>=0.1,<0.2`; the path in `[tool.uv.sources]` stays, because it is a checkout's
  convenience and never reached the wheel. `release.yml` also builds the console before packing:
  the two browser pages are gitignored build output, so a wheel built straight after a checkout
  carried none of them and a `pip install pinecall` gateway answered every screen with "run
  `scripts/console`" — which is the one thing a person who installed a package cannot do.
- **`docs/from-zero.md` was walked from zero, on a clone nobody had touched, and three steps did
  not work.** The page now opens at three `git clone` lines; the gateway comes BEFORE the first
  person, because `init` is an HTTP call to it and on an empty machine it had nothing to knock at;
  `scripts/console` is a step, because a clone has never built the two browser pages and the
  invitation link answered with the sentence saying so. It also says which key you actually need:
  the text session builds no ears and no voice, so `ANTHROPIC_API_KEY` alone carries you to
  §Spoken calls — verified by running the whole page with an `.env` of three lines.
- **A release is a tag, and the tag has a guard in front of it.** `release.yml` fires on `v*`:
  `guard` refuses unless the tag and `_version.py` say the same number, `gates` runs the same
  `ci.yml` every push runs — a tag is not a branch, so without that the release path had no gate
  at all — and only then does `publish-pypi` touch the registry, over OIDC with no token stored
  anywhere. `scripts/the-version` is the second half of the guard: it refuses while
  `pinecall-protocol` travels without a version range, because the path in `[tool.uv.sources]`
  resolves the repo next door on a laptop and does not travel in the wheel. Protocol publishes
  first, always.
- **`docs/a-box-in-production.md`: one machine with a domain, from an operating system.** Now
  with a screenshot of **every screen of both pages, in both themes** — forty-two, taken with
  Playwright against that same box, each with a paragraph saying what it is for: the console's
  seventeen and the operator admin's four. A reader sees the theme their own
  machine is in, because each one is a `<picture>` and the console follows
  `prefers-color-scheme` itself. `scripts/screenshots` is how they are taken, so the next
  redesign is one command and a diff: it signs a browser in with the one-use code
  `pinecall start` prints, walks the list of screens, and photographs each in both themes —
  no key is typed into the page and none is ever on one. Nothing is mocked — it is the page
  reading its own doors. Written
  the only way such a page is worth anything — by deleting a working box's units, app, containers
  and own secrets, and putting it all back with `make deploy` while writing down what actually
  came out. cloud-init, the deploy's five steps, the doctor, the vendor keys by fingerprint,
  `init`, Clínica Norte answering a written call against it, and a number. It found the three
  deploy gaps above, and its last table is every refusal met on the way.
- **`docs/from-zero.md`: the walkthrough, run end to end.** A runtime on a machine with nothing on
  it, through to the clinic answering a written call — every command actually run against a
  database made for it, with the real output under each, including the refusals. It found two
  things on the way: `keys revoke` could not take the fingerprint `keys list` printed, and `orgs
  move` left an agent's numbers behind.
- **`make deploy` can rebuild a box that has only its operating system.** Three things it assumed
  cloud-init had done and never did itself, each found by wiping a real box and putting it back:
  `/opt/pinecall/app` is root's to make, so a deploy into a box that lost it died on a bare
  `Permission denied`; `pinecall-secrets` is `WantedBy=multi-user.target`, so the box's own
  keypair, ops key and vault key were made at BOOT and nowhere else — a box that lost them sat
  there with a postgres that could not read `media.env` until somebody rebooted it; and the media
  plane is four Quadlets that come up from their own `[Install]`, so on a box that had not
  rebooted since the units were written, LiveKit was simply down and the doctor said so. The
  deploy now makes the directory, starts the secrets, and starts — never restarts — the media
  plane.
- **`docs/the-runtime-cli.md` documents every group there is.** It had a section for
  `pinecall-runtime chat`, a verb deleted with the dev key it existed to spend, and none at all
  for `init` or `providers`. `migrate --post` was missing too — the escape hatch for a migration
  too slow for the five seconds a unit gives it at startup, which is exactly the flag somebody
  reaches for under pressure. Checked by walking every group's own `--help` against the page.
- **`pinecall-runtime init`: the first org and the first person, in one command.** What replaces
  the magic key on a fresh runtime — it makes the org, invites its first admin, makes them an
  operator of this box (somebody has to be able to make the second org), and prints the link that
  opens the password screen and the two lines to type next. `--org` defaults to `default`, the org
  the schema seeds, so a runtime that has just been migrated needs nothing but a person. Running
  it twice carries on to the person rather than stopping at the org, because it is the verb
  somebody runs twice while reading the README.
- **`PUT /v1/numbers/{number}/env`: a number moves between the worlds.** An org buys ONE number,
  so a team wanting to try a new agent on the real line had nowhere to try it — a second number is
  a second bill, and a third world would be a third of everything. The move is one row
  (`routes.env`), so it answers from the next call on, and the carrier account and both trunks are
  untouched: a call arrives at this box whichever world answers it. The one numbers door that does
  not work in the key's world alone, because crossing the two is what it is for.
- **An admin and the box operator see every sandbox corner.** A sandbox agent is held per person,
  which is what stops two developers taking each other's `pinecall run` — and it also meant nobody
  could see anybody else's: a tenant's admin had no way to tell what their team was running, and
  the operator of the box had none either. `GET /v1/agents` now answers a key that opens `team`
  with one row per CORNER instead of one per slug, and every row carries `holder`, the member
  whose copy it is — absent for the org's own, which is what a server's token holds. Which rows a
  reader gets is the key's own answer (`sees_every_corner`, `auth/keys.py`): whoever may see who
  the team IS may see what the team is RUNNING. `holder` is the same `{holder, name}` the line
  door answers with, because the id alone names nobody a page can show.
- **`orgs move <agent> <org>` takes the agent's NUMBERS with it.** A slug belongs to the org that first registered it for as long as
  its log exists, and nothing could move it — so an agent registered from a terminal pointed at
  the wrong key belonged to that org for good, with every call it went on to take. A box walks
  into it by construction: its own worker and operator keys are issued into `default`, so the
  first agent anybody runs there lands in `default` too. The verb moves the agent's own log and
  one head row per call; it is refused while somebody holds the slug, and 404s one nobody ran.
- **Every vendor LiveKit reaches, not five.** `providers/catalog.py` is one table of the
  forty-five vendors livekit-agents 1.8 ships a plugin for, and `providers/plugin.py` builds any
  of them out of the plugin's own constructor signature — so `stt: cartesia`, `tts: rime`,
  `llm: groq` need no file here and no edit when livekit adds one. The five under
  `providers/llm/`, `stt/` and `tts/` stay: they are the vendors this build has an opinion about.
  `pip install pinecall[runtime,providers]` (and `providers-big` for AWS, Azure, Google and
  Speechmatics) is what makes a row real; a vendor with no plugin is refused by name with the one
  command that installs it, and a vendor that wants more than a key — an endpoint, a client pair —
  is refused in its own words, before the call rather than mid-turn.
- **`livekit` is a vendor.** LiveKit Inference fronts OpenAI, Google, Deepgram, Cartesia,
  AssemblyAI, Inworld, xAI and the rest on the box's own LiveKit project: no vendor key, no extra,
  nothing to install. `llm: livekit` + `openai/gpt-5-mini`, `tts: livekit` + `cartesia/sonic-3`.
- **Aliases.** `11labs`, `claude`, `gemini`, `grok`, `gpt`, `dg`, `mistral` and the rest resolve
  to one canonical name at every door — a declaration, an override, a stored key — so a tenant who
  brought a key under one spelling reads it back under the other.
- **`GET /v1/providers`** and **`pinecall-runtime providers`**: the whole catalogue with what each
  vendor still wants on THIS box — plugin, key, or nothing. Never a key, not even a prefix. The
  same rows ride inside the pipeline report, so the console's two screens cannot disagree.
- **A `tts` knob at the pipeline door.** The speaking stage was the one an operator could not
  move: the voice and its model could be turned, the vendor could not. `tts: cartesia/sonic-3`
  turns it like the other two (migration `0022`).
- **A price for the other forty vendors.** `providers/published_prices.json` — a thousand models
  off [mahimailabs/voice-prices](https://github.com/mahimailabs/voice-prices), dated per row and
  regenerated by `scripts/refresh-prices` — sits behind the hand-read table, which still wins
  where the two disagree. A call spoken by Cartesia used to read `unpriced`, which in a summary is
  a bill nobody can see.
- **The migrations got a guard.** `schema_migrations` keeps a **sha256** and refuses a checkout
  whose applied file changed: the rule against editing one is now a gate, and it is the only thing
  that catches two databases silently disagreeing. Every run takes an **advisory lock** first,
  sets a **5 s statement and 1 s lock timeout** per migration, and **says which database** it is
  talking to; `migrate status` asks the database rather than listing the disk, `migrate plan`
  touches nothing, and a `.post.sql` never runs at startup. `migrations.lock` names the last
  migration that landed — bumping it makes two branches adding `0022` conflict in git, and it is
  the baseline for **squawk**, which gates every unlanded migration in `scripts/lint`. And
  `tests/migrations.py` builds a schema as a box HAD it, with rows, so 0021 is proven not hoped.
- **`GET /v1/whoami` carries the org's `slug`** beside its id: `org` is what doors take, `slug` is
  the word its people read, and `pinecall login` was printing the id at them.
- **A terminal is signed in from a browser.** `pinecall login` holds no key and the person at it
  has none to paste, so the two meet at a word: four doors under `/v1/login/pairings` — the
  terminal mints one and polls, the browser reads and approves. That mints the TERMINAL's own key
  — same person, sandbox, labelled as that machine — and a password is typed into a page and
  never into a shell. `docs/protocol/people.md`.
- **In the sandbox, a contact's facts and a knowledge base are one DEVELOPER's** (`0021`). 0018
  gave both tables the world they were written in; the sandbox was still one pile shared by the
  team, so one developer's `knowledge push` replaced what the other two were testing against and
  one test call's extracted fact arrived in another's. Both now carry whose corner wrote them.
  Knowledge **falls back** — a developer who has pushed nothing reads the org's, because nobody
  joins a team to an empty base — while a push, a drop, and memory never do. The quotas still
  count every corner: the rows are the org's.
- **Where a ring lands in the sandbox, in two steps.** An org shares one sandbox number, so
  three developers on one agent meant the newest `pinecall run` silently took the others' calls,
  answered in a colleague's scrollback with nothing saying so. Now a developer says which number
  they call FROM (`PUT /v1/line/from`) and every call they make lands in their own corner — three
  of them test at once with no coordination — and a number nobody claimed falls back to the
  agent's **line** (`GET/POST/DELETE /v1/agents/{slug}/line`), which the first corner to hold it
  takes and which is handed on when that terminal closes. Neither is a row: both are only
  meaningful next to a socket. Production has one corner. `api/agents/doors.py`.
- **The operator invites an org's first person.** `POST /v1/ops/orgs/{named}/members` and
  `pinecall-runtime orgs invite <org> <email> --name … [--role]`: how a tenant exists at all on a
  gateway that takes no sign-up — the box makes the org and invites its admin, and prints a
  **link** once that opens the console's password card. The operator holds a token and never a
  password, the invitation takes none of the org's seats, and there is no door that changes or
  disables a member from the box: an invitation is inert until the person it names accepts it.
- **`GET /v1/ops/whoami`**, where an operator's key is proved: `{operator, version, domain, name,
  org}` — the name and the org when a person's key knocked, null for the box's own. `api/console.py`
  becomes `api/pages.py`, and `GET /v1/ops/fleet` carries `stale_after_s`, the hub's own threshold.
  (This shipped with a second browser page at `/admin`, built from the agents repo's
  `src/cli/ui/admin/`. That page is gone — see **Removed** above — and nothing but this door
  outlived it.)
- **`seats`, the seventh quota.** How many people an org may hold: invited and active together,
  because an invitation sent is a seat taken, and a `disabled` member keeps their row and holds
  none — which is what frees one. `POST /v1/members` counts before it writes and answers the
  quota's own `429`, writing no `credits.exhausted` entry for the reason a push writes none: an
  invitation names no agent, and an agent's log is the only log an org has. A seat is charged only
  where a row will be made, so re-sending a link to somebody still invited is never the thing a
  full org cannot do. `0019`; `--seats` on `orgs quota` came free of `QUOTAS`.
- **An org issues its own keys.** `GET /v1/keys`, `POST /v1/keys` and
  `POST /v1/keys/{fingerprint}/revoke` on the org's own key (`keys`): the key a tenant's server
  runs on no longer has to come from the box's operator. A POST mints a key for a MACHINE —
  `app` and production when nothing is said, naming nobody, because people get keys by logging in
  — and answers it in the clear the once; the listing is fingerprints and never a key; a key may
  not issue a scope it does not itself open, and another org's fingerprint is the 404 a stranger's
  is.
- **`providers`, a scope of its own.** The vendor keys an org brought are `providers` from here;
  `keys` is the org's own API keys. `0017` hands `providers` to every row that held the old `keys`,
  so nothing a live key could do yesterday is refused today. `manager` and `admin` preset both.
- **`extensions/`, and no plan in the runtime.** The points a package installed beside the runtime
  plugs its policy into, named by `PINECALL_EXTENSIONS` and imported at startup — a name that does
  not import stops the start rather than admitting orgs without limits. One point today:
  `admitted(org, email) -> Quotas`, asked at the sign-up and written in the same breath the org is
  made. The runtime's own answer is no limit and no row. The free-trial numbers that lived in
  `api/signup.py` are gone: a trial is a plan, and a plan is the charging package's to spell. The
  cut is sentry's and getsentry's; ARCHITECTURE §12 says how.
- **`PINECALL_SIGNUP`, and the door it opens.** `POST /v1/signup {org, name?, email, person,
  password}` makes an org allowed whatever `extensions.admitted` answers, its first `admin` active
  with that password, and answers their first key with a one-use login code for the console —
  throttled five a minute per client. Only where the setting is on, and it is **off unless set**: a
  box somebody runs for their own agents is never asked to close a door, and a gateway that takes
  none refuses `403` naming the setting. Its own flag and not `cloud` — a box of its own may want
  sign-ups, and a cloud may close them. `GET /.well-known/pinecall` answers `{version, cloud,
  signup}`, which is what the console reads to decide whether to draw a way in. The gateway sends
  no CORS header at all: the console it serves is the same origin, and a site elsewhere links.
- **Numbers the box buys for a tenant.** `POST /v1/numbers/buy {country, area_code?, agent}` finds
  one local voice number on the box's own Twilio (`TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY`,
  `TWILIO_API_SECRET`, the names `twilio_trunk.py` already reads; the gateway unit imports them),
  buys it, attaches it to the box's trunk `pinecall` and wires it as an import does; `?dry_run=true`
  names the number and pays for nothing. The route carries `managed: true`, and a new quota
  **`numbers`** (`orgs quota --numbers`, migration `0016`) caps how many the box buys for an org —
  imported numbers count against nothing. `credits.exhausted` may now name `numbers`.
- **A tenant's own numbers.** `PUT /v1/carrier` brings the org's Twilio account (verified once) or a
  SIP peer, sealed under the vault key; `GET /v1/numbers/available` lists what the account owns;
  `POST /v1/numbers {number, agent}` imports one — the carrier's trunk `pinecall-<org>` pointed at
  `sip:<PINECALL_DOMAIN>:5060`, the number attached, the org's LiveKit inbound trunk admitting it
  from the carrier's networks, the route — each write looked up before it is made, and
  `?dry_run=true` answers the plan alone. `DELETE /v1/numbers/{number}` lets one go. Migration
  `0015`; `PINECALL_DOMAIN` is now read by the gateway. `docs/protocol/numbers.md`.
- **The org's floor.** `GET /v1/sessions?limit=` lists the org's newest calls across every agent,
  the same rows as an agent's own list, each now naming its `agent`; `GET /v1/events` streams the
  floor changing — an agent registered or detached, a call ringing, dialing, started, ended — as
  SSE, live only, each frame the entry of its own log. Both on a key with `calls`.
- **The org's tables on the tenant's key.** `GET /v1/usage` (the org's metered rows and totals,
  scope `usage`) and `GET /v1/numbers` (its doors in the key's world with their source, scope
  `numbers`).
- **`agent.detached`.** A socket that held an agent and went is written to the agent's own log:
  which socket, which world, and whether anybody still holds the agent there.
- **The gateway serves the console.** `GET /` and every path that is not a door's answer the
  built page from `src/pinecall/gateway/console/` — package data `scripts/console` builds from
  the agents repo and copies in, git-ignored, carried by the wheel and by `make deploy`, which
  now runs it first. `/v1/*` and `/.well-known/*` paths nobody declared are still JSON 404s, and
  a gateway nobody built the console into says so in a sentence. `GET /.well-known/pinecall`
  answers `{version, cloud}`; `PINECALL_CLOUD` says whether this is Pinecall's hosted gateway.
- **The console's directory verbs go over the app socket.** `POST /v1/agents/{slug}/dev/{family}/{verb}`
  relays a console's ask — a written call to the class, the personas and a simulation, the goldens
  and a suite, the knowledge folder, the memory goldens, a promoted candidate, drift, a
  reproduction — as an ephemeral `dev.request` down the socket of the `pinecall run` holding the
  agent, and answers with the `dev.answer`'s result or refusal verbatim. What `pinecall ui` served
  from its own process under `/ui/*` now comes through the gateway. `docs/protocol/dev-verbs.md`.
- **The doors enforce scopes, and the seat is named.** Every tenant door asks the key for exactly
  one scope — `app`, `calls`, `talk`, `supervise`, `pipeline`, `knowledge`, `memory`, `evals`,
  `keys`, `team` — and refuses with `403 this key does not open X: it opens …`; both sockets
  close with the same sentence. A test walks the app and fails on a door that declares none or
  two. A supervisor's seat minted from a person's key carries the member's id and name, so
  `supervisor.*` entries say who; `GET /v1/whoami` and the seat's answer carry `subject` and
  `name`.
- **Members and login.** An org's people are rows: `POST /v1/members` invites one with a one-use
  token that dies in a week, `POST /v1/invitations/{token}` accepts it with a password (argon2id at
  rest) and answers the person's first key, `POST /v1/login` mints a key for a person and a device
  from org, email and password — one `401` sentence for every wrong thing, five tries a minute per
  name — and `POST /v1/login/codes` mints a one-use code a key holder hands a browser so no key
  ever rides a URL. Roles are presets of key scopes: `qa` · `supervisor` · `manager` · `admin` ·
  `developer`. Disabling a member revokes their keys. Migration `0014`; `argon2-cffi` joins the
  dependencies.
- **The key knows where and who.** An API key is issued into one of two worlds — `production` or
  `sandbox` (`keys issue --env`) — and the gateway namespaces its registry and its routes by
  it: the same slug is held once in each, `GET /v1/agents` and `GET /v1/routes` answer the key's
  world, a call opened on the other world's route is `403`, and a sandbox key claiming a number
  production holds is refused with the world named. `agent.registered` and `call.started` carry
  `env`. The key also carries `scopes` (the doors as they are grouped; `--scope`, repeatable, every
  scope when left out), and `subject` and `name` for a person's key; `GET /v1/whoami` answers all
  of them. `routes add --env` types a number into a world. Migration `0013` leaves every existing
  key production's with every scope. The dev key opens the sandbox. The worker's tools door now
  takes the org's key like every other worker door.
- **A call's first entry names the run that opened it.** `call.ringing`, `call.dialing` and
  `call.started` carry `run`: the eval run's id, or null for a person. It replaces a caller id
  prefix (`eval_…`) that three processes read as a marker — the worker, to greet nobody on a call
  that opens mid-conversation; the tenant's CLI, to seed the golden's state; the gateway, to mint
  it in two places. The fact is now on the dispatch (`run`) and on the wire, and a golden's caller
  is the visitor id every caller with none gets.
- **The worker reads `~/.pinecall/dev`.** A gateway on a dev key leaves its door there and the
  tenant's CLI already read it; the worker now does too, from `pinecall.auth.dev_file`, and knocks
  with that key when its gateway is that door — saying out loud that an exported
  `PINECALL_API_KEY` is being ignored. Which key a worker sends no longer depends on whether the
  url happens to be loopback.
- **What a broken golden's model was asked rides the cell.** `Spoken` and `Run` carry `asked`;
  the row's JSON writes it only under a cell that broke, and writes `null` when the run kept no
  requests — a spoken run builds them in the worker — where an empty list used to stand for both.
- **A declared greeting is spoken.** `AgentConfig.greeting` had been on the wire since ms-2 and no
  session ever read it: a string stored, overridden at the pipeline door, drawn in the console, and
  never said out loud. It is now a `Greeting` — exactly one of `say` (the words, read out as
  written, no model in the loop) or `reply` (what the model is told before it finds its own, which
  the caller never hears) — and both doors run it the moment the session can speak. They are
  `agent.say` and `agent.reply` declared instead of called; nothing new was invented on the wire.
  A class that declares nothing waits for the caller, as before.
  `session/greeting.py` holds the choice once and each door hands it its own pair of verbs.
- **The write side of memory is held to goldens of its own**, which is the half that persists: the
  hang-up makes ONE model call and it can miss what mattered, invent a fact, leave two versions of
  one fact standing, or write a category the tenant listed as never-keep.
  `POST /v1/agents/{slug}/memory/extraction` takes cases — a call already written down, plus what
  memory already holds — runs that very extraction per case on the org's own model and keys, and
  asks four questions of what came back, all by code: every category named got a fact (the words
  are the class's own `memory.remember`), none went under a `forget` category, no fact's TEXT
  carries a value the call showed must not survive (`never_says`, matched on the folded words and
  on the digits alone), and exactly the held facts the call contradicted were superseded — the
  mirror included, which catches a model that replaces whatever it touches. A case may also PLANT
  sentences: planting one is the assertion that admission refuses it. `memory/goldens.py`,
  `api/extraction.py`.
- **Memory can be held to a golden**, the way a base already can, and it is the only thing that
  says `recall` returned the wrong facts: a ring watches a conversation and only ever sees the
  facts memory handed over, never the better one it missed. `POST /v1/contacts/memory/eval` takes
  questions that bring their own facts — `{holds, asks, expects}` — writes each question's facts to
  a scratch contact of the org, recalls, deletes them, and answers `recall_at_k` and `ndcg_at_10`
  by code with no model, plus every question it did not answer whole. Writing them is what makes
  the figures the real ranking: the same two index scans, the same fusion, the same embedder a
  call uses. A fact answers when what came back CONTAINS what was expected, folded for case and
  accents, because a fact is a sentence a model wrote and a golden names the substance.
  `memory/scoring.py`; the arithmetic behind both figures lives once in `types/goldens.py`.
  `Memory.hold` is the write with no model in it. `docs/retrieval/spec.md` has the contract.
- `Golden.memory`: a ring-1 golden may open its call already knowing things about the caller.
  `evals/remembering.py` answers those facts to the `recall` tool for that call and nothing else
  moves — the tool call, the result and the request are real, the memory table is neither read nor
  written, and `remember` at hang-up is still the gateway's so a run writes no fact about
  a caller nobody called as.
- The runtime, from zero: one distribution, two processes (gateway, worker) on livekit-agents 1.8;
  the log with a seq born under the database; orgs, hashed API keys with scopes, quotas, usage,
  routes, the provider-key vault, the token door, WhatsApp's webhook, the operator API.
- Sessions on both channels — voice in the worker, text in the gateway — writing the same log
  under the same names, with every metric livekit measures.
- Evals: the four rings on `livekit.agents.evals`; every finished call judged at hang-up
  (`call.score`), consent and grounded on the panel; ring-3 checks by code; ring-1/2 goldens.
- The CLI: `gateway`, `worker`, `sessions`, `chat`, `orgs`, `routes`, `keys`, `migrate`, `doctor`, `box`.
- The declared box: cloud-init on any provider, systemd units, Quadlet containers, nftables,
  every secret an encrypted systemd credential; roles `all` / `hub` / `worker`; worker slots
  (`PINECALL_MAX_JOBS`).
- The deploy as a Makefile: `make deploy` (rsync, `make -C infra/box install`, `uv sync`, restart
  by role, health, doctor), `make secret`, `make worker-secrets`, `make doctor`, `make status`, `make logs`.
- The doctor knocks at every vendor with the key the box holds and fails a deploy on a dead one,
  naming the variable and never the value; it knows the box's role (`PINECALL_ROLE`).
- `ARCHITECTURE.md`, `docs/protocol/` (operator API, the token door, the projections).
- The seam memory and retrieval land on: **two declared tools the platform runs**, `recall` and
  `search`. The class's declaration brings each one — `memory` brings `recall`, `docs` brings
  `search` — they stand in the request's `tools` array beside the app's own, and their answers
  reach the model as `tool_result` blocks. With `docs.mode = "retrieved"` (the default) and
  whenever `memory` is declared, the session runs the lookup itself and puts a real `tool_use` /
  `tool_result` pair into the request — on a spoken call while the caller is still talking, under
  `PINECALL_VOICE_LOOKUP_BUDGET_MS` (250), and on a written one at turn end under
  `PINECALL_TEXT_LOOKUP_BUDGET_MS` (3000); with `docs.mode = "tool"` the model calls `search`
  itself. At hang-up the call is remembered under `PINECALL_REMEMBER_BUDGET_S` (8.0).
  `AgentConfig` declares `knowledge`, `docs` and `memory`; the worker asks
  `POST /v1/calls/{call}/lookup` and `/remember`. A lookup that did not run is a recoverable
  `error` entry (`recall_skipped`, `search_skipped`, `remember_failed`), and the call goes on.
- **A spoken call's lookups start while the caller is still talking.** `recall` and `search` ran
  when the turn ended, inside the caller's silence, and on a live two-turn call every one of them
  was skipped: the same door that answers in 111 ms idle took up to 1085 ms against the reply the
  session was already generating, and 250 ms of a telephone line is all a turn can spend. Now the
  first interim transcript carrying four words starts the run (`TurnLookups.heard_so_far`), one
  per turn; the end of the turn collects it — nothing to wait for when it is back, its tail under
  budget when it is not. The budget is per tool and per channel now:
  `PINECALL_LOOKUP_BUDGET_MS` is gone, replaced by `PINECALL_VOICE_LOOKUP_BUDGET_MS` (250) and
  `PINECALL_TEXT_LOOKUP_BUDGET_MS` (3000). Measured over six two-turn calls each way, the caller
  waited 125–251 ms per turn before and 0 ms on five turns of six after. The *retrieval*
  decision page, in the maintainer's notebook.
- Memory itself: `memory/` and `0008_memory.sql`. `PgvectorMemory` keeps a contact's facts in
  `contact_memories`, bi-temporally — an update is a new row that supersedes the old one, an
  invalidation an end date, nothing is deleted but by `forget`, the right to be forgotten.
  `recall` runs cosine over `halfvec(1024)` and BM25 through pg_textsearch (`spanish`), fuses the
  two by rank (RRF, k=60), weighs recency (half-life 90 days) and confidence, and answers the best
  k at 0..1 with no model; `remember` is one call to the org's own model at hang-up, answering
  add / update / invalidate ops, parsed strictly and policed by the tenant's `MemoryPolicy`
  (a `forget` category never reaches the table, and a fact that names one of the class's own tools
  is refused before it — admission at write time, checked against `AgentConfig.tools`).
- The knowledge base (`knowledge/`, migration `0009_knowledge`): a tenant's Markdown files
  chunked by heading under ~350 tokens, each chunk under its heading path, embedded in batches
  and kept in `knowledge_chunks` with an HNSW index by cosine and a BM25 index in spanish; a push
  replaces the base whole in one statement, and a search fuses both indexes by reciprocal rank.
  The row in `knowledge_bases` says which model wrote the vectors.
- The lookup itself (`lookups/`): one `Lookups` per gateway is the session's `Lookup` and
  `Rememberer` for every text call in-process and, over `POST /v1/calls/{call}/lookup` and
  `/remember`, for every spoken one. `recall` reads the contact from `CallContext.remembered_as`
  and never from what the model wrote in the tool's input; both are written on the call's log.
- The knowledge base's doors on the tenant's key (`PUT`/`GET`/`DELETE /v1/knowledge[/{base}]`,
  the base replaced whole) and a contact's (`GET`/`DELETE /v1/contacts/{contact}/memory`).
- The licence where an operator meets it: the copyright line filled, a License section in
  `README.md`, and `license-files` putting the text in the wheel and the sdist.

### Changed
- **A written call keeps no recording.** A `chat` visit has no ears and no voice, and the room
  carries no audio, yet the worker asked the session to record and the sealed `call.summary`
  pointed at an `audio.ogg` nobody wrote — a session screen that said the file was on another box.
  The worker now hands a written call no recording directory at all, so its summary points at
  none, `GET /v1/calls/{call}/recording` answers `404` in a sentence (`kept no recording: its
  call.summary points at none`) and `sessions recording` says so (`call … was not recorded: its
  call.summary carries no path`) and exits 1.
- **A letter has a logo above its card, or nothing.** A box told no `logo_url` used to write its
  name in plain type where the logo goes — "Pinecall" over every letter of a box nobody had
  branded, a header that said less than the footer already does. The row above the card is now
  drawn only when there is a logo to put in it; with none, the card is the top of the letter. The
  `alt` of the logo stays the name, for a client that blocks images.
- **`pinecall-protocol>=0.3,<0.4`.** The runtime requires the protocol that documents
  `agent.transcript` as a delta and whose dial guards carry no `countries`; the checkout still
  reads the repo next door through `[tool.uv.sources]`.
- **`PINECALL_DEV_KEY` is gone, and with it the second runtime a laptop was.** One string in the
  gateway's own environment that needed no database and, when set, was the ONLY key the gateway
  honoured: every call org `default`, the `api_keys` table not read, `~/.pinecall/dev` written at
  every start so the CLI beside it needed no login, a LiveKit pair derived from it that signed
  tokens opening no room, and a worker that took that file's key over the one it was issued. It
  bought five minutes at the start and charged them back in every hour after — two sets of keys,
  two orgs, two behaviours, and no way to see which you were on. A worker that died for fifteen
  minutes on `GET /v1/routes: 401` (2026-09-11) was that, and so was a deployed one sending
  `Bearer ""`. A laptop now runs the same Postgres, the same migrations and the same issued keys a
  box does; `pinecall-runtime init` is the way in, and `pinecall-runtime chat` — which existed
  only to spend that key — is gone with it. A gateway with no database verifies nothing, says so
  at startup, and answers every keyed door `503` rather than coming up looking healthy.
- **The world things are written in is `sandbox`, not `development`** (`0023`). The word was doing
  two jobs — naming a world, and naming "mine" — so a team that wanted a shared staging deployment
  had nowhere to put it, and a person reading `env: development` could not tell a laptop from a
  box. The migration rewrites the five tables that carry the column and their CHECKs; `--env
  development` is gone from `keys issue` and `routes list`, and `is_a_deployment(env)` is now the
  one question the three places that used to compare against `production` ask, so the day a second
  shared world exists they already mean the right thing. A shared staging needs no third world: a
  machine key in the sandbox names nobody's corner, and that is exactly what every member sees.
- **A sandbox registration is ephemeral.** `agent.registered` and `agent.detached` were appended to
  the agent's log in both worlds, and an agent's log is one log for all of them — so a laptop
  reconnecting all afternoon buried the deployed agent's history in its own noise. In the sandbox
  the entry is marked ephemeral; production is unchanged.
- **A tenant can deploy.** `POST /v1/keys` measured the ask against the asking key's scopes, and a
  person's key in production does not carry `app` — so an admin asking for the key their own
  server runs on was refused, in both worlds, and only the box operator could mint one. The bound
  is now the person's ROLE. A key naming nobody is bounded by itself, as before.
- **`PINECALL_API_KEY` is `PINECALL_WORKER_KEY`.** One name was three things: the worker's
  credential, a key source in the v2 CLI, and the variable v1's SDK exports — so a laptop with
  v1's export still live registered agents into whatever org that key named, silently. The
  runtime's half of the collision is gone. A box that ran before the rename mints a fresh worker
  key on the next deploy and leaves the old credential behind; infra/box/README.md says what to
  remove.

- **A voice id belongs to whoever was named.** A declaration that names its TTS provider has its
  voice passed through as that vendor's own id: Cartesia writes a uuid, Rime a word, Hume a
  sentence, and this build knows only ElevenLabs' shape. A declaration that names NO vendor is
  judged exactly as before — a curated name or a twenty-character id — because the vendor it will
  speak with is the one whose shape is known.
- **A bare word at a model knob that names a vendor IS the vendor.** `stt: cartesia` moves the
  stage and keeps that vendor's own default model; `claude-sonnet-4-5` alone still means a model
  on the vendor already in use. No alias is also a model name, so the two readings cannot collide.
- **The ElevenLabs model list is ElevenLabs'.** It used to be applied to every TTS model an
  operator typed, which refused `sonic-3` on Cartesia.
- **The doctor asks the catalogue.** A role is down when NO catalogued vendor of that role has a
  key, so a box running on Cartesia and Groq reads green without a row being added anywhere.
- **A contact's facts and a knowledge base are one world's.** `contact_memories`,
  `knowledge_bases` and `knowledge_chunks` carry `env` (`0018`, everything already written is
  production's), and every read and write says which: a test call on a laptop no longer writes
  facts into the memory a production call reads under the same number, and a `knowledge push`
  with a sandbox key replaces the sandbox base and never the telephone's; production's is the
  same push made in production. The `kept` counts the quotas read take both worlds,
  because a row a laptop wrote is a row on the same disk.
- **An agent is held per person in the sandbox.** The registry's name for a holding is
  `(env, holder, slug)`: nobody's corner in production, where what is deployed is the org's, and
  the member the key was minted for in the sandbox. Two developers of one tenant now each run the
  same agent on their own laptop and neither takes the other's — before this the second
  `pinecall run` replaced the first, and every `pinecall chat`, every suite and every config read
  followed whoever had started last. A sandbox key that names nobody (CI's) holds the org's
  own, which is what a developer holding none falls back to. A **dialled** door is namespaced by
  neither: a number exists once in a world, so the shared sandbox number is answered by the
  newest run, and `GET /v1/agents` lists what the reader can actually reach — never another
  developer's socket.
- **One rule for "a call a run opened has no opening".** Both sessions ask `the_greeting_for`
  with the call's `run`; the eval runner no longer rewrites the class's config with `greeting=None`.
  The three first entries of a call (`call.ringing`, `call.dialing`, `call.started`) are built in
  one module, `session/first_entries.py`, instead of three copies.
- **The evals surface test and nine evals test files are under the `unit` mark**, so `pytest -m
  unit` runs them: 120 tests the gate had been skipping, one of them red (`__all__` had grown by a
  judge the pinned list did not have). `NoVoice` left the public surface; nobody imported it.
- **The simulated caller is on the line before anybody picks up.** Its track is published at
  connect, at 48 kHz, and only then is the agent waited for: a track opened and pushed into in one
  breath handed the agent a line already playing, and the 1.7 s it took to subscribe were the whole
  first sentence (`identifica-al-paciente`, one spoken run in three). `evals/speech.py` returns
  every line at that one rate — espeak-ng's own rate is resampled by livekit's `AudioResampler`.
- **The spoken run decodes `agent.state` through the protocol** (`AgentStateChanged`, typed
  `AgentState`), instead of reading a raw dict key against a bare string.
- **A base can be held to a golden**, which is the only thing that says the index missed a BETTER
  passage — the judge that runs on every call can only weigh what the model was given.
  `POST /v1/knowledge/{base}/eval` takes the questions and the chunk each should have found, and
  answers `recall_at_k` and `ndcg_at_10` computed by code with no model in the loop, plus every
  miss with what came back instead. A base listing now names the embedder that wrote its vectors,
  so a tenant learns of a mismatch from the list and not from a 409 at the next turn.
  `docs/retrieval/spec.md` is the whole contract: the four numbers already in the log, what each
  targets, and the mapping onto OpenTelemetry GenAI's retrieval, memory and embeddings spans.
- **What a lookup found is a tool result, not a piece of the prompt.** The view's markers
  (`<!-- memory: … -->`, `<!-- retrieved: … -->`, `<!-- knowledge: … -->`) are gone, and with them
  `types/markers.py`, `TurnFills`, the `Filler` protocol and `POST /v1/calls/{call}/fill`. They
  spliced a model-written fact and a chunk of somebody's document INTO the tenant's own view, and
  the view travels wrapped in `<instructions>`: one blob, three authorities, presented as an
  instruction. Both vendors say not to. Now `recall` and `search` are declared tools whose
  descriptions say what the content is and where it came from, their answers are JSON objects
  inside `tool_result` blocks, and the dynamic region of the prompt is the view and nothing else.
  `docs/security/prompt-injection.md` is the contract and is public. `filling/` is `lookups/`,
  `PINECALL_FILL_BUDGET_MS` is `PINECALL_VOICE_LOOKUP_BUDGET_MS` / `PINECALL_TEXT_LOOKUP_BUDGET_MS`,
  and `memory_skipped` / `retrieval_skipped` are `recall_skipped` / `search_skipped`.
- **A tenant brings its own provider keys, with its own API key and no operator.**
  `PUT /v1/provider-keys/{vendor}` · `GET /v1/provider-keys` · `DELETE /v1/provider-keys/{vendor}`
  take no org — the key IS the org — and the listing is vendor names and never a value. The vault,
  the mechanism and the one door that answers with a key are unchanged; what is new is that the
  operator is no longer the only writer. `/v1/ops/orgs/{org}/provider-keys` stays for the cloud and
  for the operator of a box. `pinecall keys add|rm|list` in both languages reads the key from
  stdin, never from argv.
- **A box can run its own embedder.** `pinecall-tei` (bge-m3) is a Quadlet unit installed only
  where `EMBED_PROVIDER=tei` in `box.env` asks for it; a hub that embeds at Perplexity or
  OpenRouter takes its key as an encrypted systemd credential instead, and a worker embeds nothing
  because the gateway is what looks up. On a hub an embedder that is down is the doctor's verdict now,
  not its advice, and the line names what to fix. A hub that becomes a worker STOPS the media
  plane it may not disable: `systemctl disable` refuses a generated unit before it would have
  stopped anything, so the containers were outliving the role that owned them.
- **Two quotas more, of the same kind, so a plan can switch memory and the knowledge base off:**
  `memory_facts` and `knowledge_chunks` on `quotas` — NULL is no limit (what a self-hosted box
  has), `0` refuses everything, N is a cap. `orgs quota` gains `--memory-facts` and
  `--knowledge-chunks`; `PUT /v1/ops/orgs/{org}/quotas` gains both fields and
  `GET /v1/ops/orgs/{org}` answers `holding` beside them. A knowledge push past the cap is refused
  429 before a row is written; a hang-up past it writes no fact and asks no model; a lookup at `0`
  answers the empty object of its own shape (`{"facts": []}`), embeds nothing and writes no entry;
  reading and forgetting a contact's memory are refused by no quota.
- **The embedder is configurable and multi-model, and the knowledge base is embedded
  CONTEXTUALLY.** `Embedder` gains `embed_documents(documents)` — one vector per chunk, one list
  per document — and `PgKnowledge.put` groups the pieces by FILE, so a chunk is embedded while the
  model sees its neighbours instead of alone.
  `providers/embed/perplexity.py` is one client for both of Perplexity's models: a name carrying
  `-context-` goes to `POST /contextualizedembeddings` (a document at a time, windowed), anything
  else to `POST /embeddings`. The encoding belongs to the vendor: Perplexity takes `base64_int8`
  and refuses `float`, OpenRouter's mirror answers floats. All three replies are unnormalised, so
  every vector is stored at unit length.
  `EMBED_PROVIDER` (`tei` · `perplexity` · `openrouter`, default `tei`), `EMBED_MODEL`,
  `EMBED_BASE_URL` and the two keys; `embedder_for(settings, http)` is the one place a provider
  name is switched on, and the doctor says which this box embeds with. TEI's CPU image has no
  arm64 build, so on an Apple Silicon laptop this is the only way to retrieve at all. The
  *retrieval* decision page, in the maintainer's notebook.
- A vector is only comparable to vectors of the same model, and both tables now say so out loud:
  `knowledge.search` refuses a base another model pushed (`base clinica-norte was pushed with
  pplx-embed-context-v1-0.6b; this gateway embeds with BAAI/bge-m3: push it again`), and
  `0010_memory_model.sql` puts `model` on `contact_memories`, which the DENSE branch of a recall
  filters on — BM25 is untouched, so an older fact is still recalled by its words.
- The prompt is named blocks in two regions: `AgentConfig.prompt` declares the layout (default
  `identity · knowledge · tools`, the history, `view`), `prompt.set {name, text}` writes one block,
  `prompt.changed` and `State.prompt` are keyed by name, and `AgentConfig.instructions` is gone —
  the identity block is written like every other. For Anthropic each static block is its own
  `system` string, so a rewritten `tools` block leaves `identity` and `knowledge` cached.
- `tools.set` no longer re-declares the model's tools (a changed tool definition empties the
  provider's whole cache): the agent keeps every declared tool for the call, the visible subset is
  enforced in the runtime's own callable, and a call to a closed tool comes back to the model as
  `<name> is not available now` with an `error refused` entry in the log, never reaching the app.
- `Rememberer.remember(call)` answers how many memory ops were written; the worker's client reads
  it off `POST /v1/calls/{call}/remember`.
- Reciprocal rank fusion, its two constants and the halfvec text literal have one home each
  (`types/fusion.py`, `providers/embedder.py:as_halfvec`); memory and the knowledge base both
  import them, and a tie in a fused order is settled by id on both.

### Fixed
- **The reducer joins the deltas of `agent.transcript` into the reply so far.** An
  `agent.transcript` entry is one delta — a word with its timings in a voice call, one model
  token in a written one — and `State.live.agent` was being replaced by each one, so a console
  watching a reply saw one word at a time and never the sentence. `reduce.py` now appends every
  delta since the last `turn.agent`: a word the voice aligned (`start` set) is set a space apart
  unless the join already has one; a written token carries its own spacing and is glued as it
  came, so "clean" + "ing" is "cleaning". `turn.agent` still clears it.
- **Three web calls were `live` for thirty hours.** `call.ended` is written by the worker holding
  a spoken call, from a shutdown callback, and the unit's default `KillMode` sent systemd's
  SIGTERM to every process of the worker — livekit's forkserver and each job process with it. They
  died within 100 ms of `systemctl stop`, so the drain that had just begun found no running job,
  the stop finished in a second, and nothing on earth ever closed those logs (box, 2026-09-16).
  Three fixes, one per layer: the unit is `KillMode=mixed`, so only the main process is signalled
  and the jobs are livekit's to drain; `worker/main.py` gives the drain ten minutes and each job's
  seal sixty seconds, instead of livekit's hour and ten seconds, both of which the unit's
  `TimeoutStopSec` cut; and the gateway runs a **reaper** (`api/reaping.py`) that ends a spoken
  call whose room the SFU no longer has and which has said nothing for five minutes — `call.ended`
  as `drained` by the platform, `call.summary`, `call.score` with `not_judged`. It runs at start
  and every minute, it never touches a quiet call whose room is alive, and it is safe from several
  gateways at once. A gateway with no `LIVEKIT_API_KEY` pair runs none and says so once.
- **`cp .env.example .env` left the runtime unable to start any process.** The example writes every
  optional knob as a bare `NAME=`, and `PINECALL_MAX_JOBS=` is the one that is an integer:
  pydantic answered `max_jobs · Input should be a valid integer` on every verb, from the file the
  walkthrough tells a reader to make. An empty value is now an absent one for every optional
  setting, so the next `int | None` knob cannot repeat it.
- **Four things the two-worlds cut got wrong, caught on review.** The agent quota counted only
  the slugs one developer could reach, so two developers each holding a different agent slipped
  past a plan of one: it counts the org's slugs across every world and corner now (`slugs`, which
  had been written and never wired). `POST /v1/tokens` and `GET /v1/routes` read the org's corner
  only, so a developer was refused a token for the agent their own `pinecall run` held: both read
  the key's corner. And `GET /v1/agents/{slug}/config` asked for `app` alone, which a person no
  longer holds in production, so the console's state panel fell back to the default in silence:
  the door opens to `app` or `calls`, the one door that does, named by path in the test that pins
  every other door to exactly one.
- **`simulate --voice` no longer talks over the agent.** The persona slept six fixed seconds
  between its lines while the golden runner waited for `agent.state: listening`; a turn that runs a
  tool takes thirteen, and the recordings had the caller speaking over the answer. The one wait is
  `api/evals/listening.py`, required of every spoken caller — the fixed silence is gone.
- `PUT /v1/knowledge/{base}` answered a bare `500` when the embedder was down, with the whole
  reason in the gateway's log and nothing at all to the tenant. `api/_refusals.py` maps
  `EmbedderUnreachable` to **503** and `WrongWidth`/`WrongModel` to **409** at every door, each
  carrying the exception's own sentence. The lookup door is deliberately not among them: a lookup
  that could not run is still `search_skipped` on the call's log and the turn goes on.

### Removed
- **Promote.** `POST /v1/agents/{slug}/settings/promote`, `POST /v1/lexicon/promote` and `POST
  /v1/knowledge/{base}/promote` are gone, with the `403` that refused a production key a set:
  production is set directly by a request that runs there. History, diff and rollback stay.
- **`POST /v1/login/env`**, and `env` on `POST /v1/login` and on accepting an invitation: a person
  holds one key, not one per world.
- **The country fence on a dial.** `DialPolicy.countries` — the calling codes an org might reach,
  empty meaning its own numbers' — is gone from the type, the guards, `PUT
  /v1/ops/orgs/{org}/dialling`, the console's dial door and `orgs dialling --country`. Which
  countries a carrier account may reach is that account's own setting (Twilio's geo permissions),
  and a second fence here only disagreed with the first. What stands: E.164's shape, the satellite
  and global-service ranges never dialled, `dial_anywhere`, the two windows and `max_duration_s`.
  The `countries` column of `dial_policy` (0032) stays in the table, written and read by nothing.
- `PINECALL_TEXT_SEARCH_CONFIG`: nothing read it. The language BM25 stems in is the index's own,
  fixed in `0008_memory` and `0009_knowledge` (`spanish`).
- `doctor --bench`: it printed that no embedder was wired. The embedder is wired; the `embedder`
  line of the report names the provider and the model and says what a down one costs (a skipped
  lookup, said in the call's log).
- `LeakageJudge`: it had no user in the tree, and a judge given a declaration nobody wrote would be
  judging a rule nobody wrote. The idea returns with the milestone that declares what another
  tenant owns.
