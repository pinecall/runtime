# Architecture — what each folder does, and what it may import

One package, `pinecall/`, folders by concept, two levels deep at most. Every fact of a call is
an entry appended to the call's log, and everything else (the app socket, the console, the
reducer, the usage feed) reads entries. The media side is LiveKit's `AgentSession`, used as is.

A module another package imports has a plain name; a module only its own package uses starts
with `_` (`gateway/_deps.py`, `session/_prompt.py`), as in the Anthropic and OpenAI SDKs. A rule
holds both directions: a `_module` is never imported from outside, and a plain one is imported
by at least one other package. The doors under `gateway/api/` are the exception, because their
surface is the URL.

| folder | what it holds | imports of ours |
|---|---|---|
| `domain/` | the words (`names.py`), an agent's declaration (`agent.py`), a call (`call.py`), an org (`org.py`), people and keys (`person.py`), the scope a request acts in (`scope.py`), the E.164 country codes (`country_codes.py`), the errors with their HTTP status (`errors.py`); no IO | nothing |
| `process/` | what one process is given and holds open: `settings.py` (every variable), `connections.py` (`Connections`: the doors' pool and the log writer's own, the vault key, an HTTP client, the LiveKit client, the signal; opened once, closed in reverse), the lossy publish and subscribe the gateway processes tell each other what just happened on (`signal.py`: Redis at `PINECALL_REDIS_URL`, or inside the one process), the state each gateway holds a share of and says whole on it every ten seconds, merged by each and forgotten thirty seconds after a gateway falls silent (`shared.py`: which app holds which agent, the lines, the phones), what the gateway counts as it works and the Prometheus text it is read as (`metrics.py`), where a call's audio is kept (`recordings.py`: the disk it was recorded on, or the recordings bucket under its org, read with byte ranges and erased there too), the object store that bucket is in (`_objects.py`: any S3-compatible endpoint, each request signed with AWS's version 4), a recording sealed under its own key (`sealed_audio.py`: AES-GCM a chunk at a time, so a range opens on its own), and the installation's own configuration kept in Postgres (`box_settings.py`: the rows the operator edits) | `domain` `postgres` |
| `wire/` | every frame, event, command, part, state, metric and the judges' score (`scores.py`) as pydantic models, one file per family; `rest/` holds the doors' bodies per family (`accounts`, `agents`, `calls`, `evals`, `fleet`, `hosting`, `numbers`, `retrieval`) | `domain` |
| `postgres/` | the pool and the migration runner, with `migrations/0001_schema.sql` beside it | `domain` |
| `log/` | a call's log: the store, the facts folded at write (`facts.py`), the queries over them (`queries.py`), the facts folded again from the log (`refold.py`: `facts rebuild` and doctor's check), the log's days (`days.py`: `call_log` is partitioned by UTC day, a week made ahead each night, a past day dropped once emptied, and doctor's check of the default), each day's drift counted at the seal (`drift.py`: the stages' histograms, `_histogram.py`, and the judges' verdicts, by agent and version; `drift rebuild`), the logs open in this process and their live readers (`logs.py`), what a call is, kept when it opens (`openings.py`: its context and the config it runs on, so any gateway serves its doors from one read), the relay (`_relay.py`: what any gateway wrote, heard on the signal and delivered to this process's readers in seq order, the org feeds and the box's floor as they come, the store asked for what arrives out of turn and polled every second while the signal is down), what a log keeps of a value its agent declared private (`private.py`: masked in the log, sealed aside for the app), the reducer, the two read projections; private: the writer (`_writer.py`: every log's appends written a group of up to 500 entries per transaction, what carries a fed type in a lane of its own) | `domain` `wire` `postgres` `process` |
| `tenancy/` | orgs, people, keys (and the keys a gateway verified lately, `remembered.py`: seconds from memory, forgotten on every gateway the moment it is revoked or its person changes), the tokens the gateway signs, the vault, admission (the quotas an org is born with and counted against, and what it used by world and month in `usage.py`: the totals the database keeps and their refold), per-scope agent tuning (`scopes.py`) and a version of it on a share of the calls (`canary.py`), the org's carrier accounts (`carriers.py`) and dial policy (the callee's hours, a number's daily count, consent and the do-not-call list among its guards; `consents.py` keeps those facts), erasure (`erasure.py`: a call, a contact or an org deleted through the one path `call_log`'s trigger admits, and its trail), the org's policy (`policy.py`: retention, its calling rules, and what a call says first, in `disclosure.py`) and the nightly retention run (`retention.py`, which also forgets call records past 24 months), a number's traceback (`traceback.py`: its calls kept or erased, and its dials), who read what (`reads.py`: a person's or the operator's read of a call, a recording or a number), a recording's own key (`recording_keys.py`: made once per call, sealed under the vault, erased with the call), the org's world exported as JSON Lines (`export.py`), caller codes, personas, an agent's own judges (`judges.py`), each distinct block of prompt its calls were told (`prompts.py`), the apps the box hosts for an org with their releases (`hosting.py`), what happens to one while it runs — stopped, its logs, the time it served (`hosted_running.py`) — and the org's secrets (`org_secrets.py`), sign-in (a password, a one-use code, a paired terminal, a sign-up; their words kept once in Postgres, hashed, their values sealed, so any gateway spends what another minted: `words.py`), the caller codes (`codes.py`, a table any gateway claims from), the sign-in knocks (`knocks.py`: counted in Postgres, five a minute per name on every gateway), the pacing of an org's requests (`throttle.py`: counted in each process and summed with what the others say every second), the org's identity provider, mail and the letters it sends | `domain` `wire` `postgres` `process` `log` |
| `providers/` | the box's providers configuration: the catalog row in `box_settings`, building a LiveKit plugin by name, the keyring a call runs on (`credentials.py`), prices, voices | `domain` `wire` `postgres` `process` |
| `session/` | one LiveKit `AgentSession` for voice and text: `session.py`, a voice call's pipeline (`voice.py`: the three stages built, and the end of the caller's turn read off the audio by a local model, livekit's `v1-mini` or Smart Turn v3 as the row says), a written one (`text.py`), the call's live state (`call.py`), the room, hold music (`hold.py`), tools, the widget channel; private: the Agent, livekit's shapes read as ours, the prompt, what the ears are told (`_hearing.py`: the turn policy per language, the keyterms) | `domain` `wire` `providers` `log` `process` |
| `retrieval/` | the embedder (`embed.py`), knowledge bases with their cutter and goldens (`knowledge.py`), contact memory (`memory.py`), a call's lookups written on its log (`lookups.py`), and what a call taught at hang-up (`extraction.py`); private: the hybrid search over one table (`_search.py`) | `domain` `wire` `postgres` `log` `providers` `process` |
| `evals/` | the case a judge reads (`case.py`), the judges and the hang-up panel (`judges.py`), the compliance judges settled by code (`compliance.py`), the checks by code alone (`checks.py`), a golden played on a written call and the judges its expectations set (`goldens.py`), real calls kept as goldens of the org's dataset (`dataset.py`), a run and its matrix (`runs.py`: one run of an agent at a time on the whole box, by a lease in Postgres the run renews), the simulated caller (`callers.py`) and its spoken line (`spoken.py`); private: what an agent could know and stated (`_evidence.py`) | `session` `retrieval` `log` `providers` and the leaves |
| `channels/` | by where a conversation comes in: `routes.py` (number or channel to agent, for every channel), `rooms.py` (a call to the fleet of its world), `whatsapp.py` (Meta's API), `telephony/` (`carrier.py`, the one module that reads an account's kind: its fence, where it is dialled, its API; `carrier_catalog.py` and `carriers.csv`, the carriers the box knows and admits; `firewall.py`, the networks 5060 opens to beyond Twilio's; `number_path.py`, what a call to a number goes through now; Twilio's API, private; the SIP trunks and rules on LiveKit; numbers imported and bought; dialling out) | `domain` `wire` `postgres` `process` `tenancy` `fleet` `log` |
| `fleet/` | the fleet each world dispatches to (`worlds.py`), the roster of workers, a worker's heartbeat and the last minute it carries (`measures.py`: what its calls' jobs tell it), the worker's client to the gateway (its batches down one socket per call, `EntriesStream`) | `domain` `wire` `postgres` `log` `process` |
| `gateway/` | the FastAPI app; private, each named for what it holds and none the logic of a door of the same name: the process's state (`_gateway.py`, the `Gateway`), each request's dependencies (`_deps.py`), what a call of an agent is set up with (`_call_setup.py`), written calls opened and taken up (`_text_calls.py`), SSE streams, the app sockets registered on every gateway of the box, this one's and the others' as they say them (`_sockets.py`); and `calls/`, what a call is to a gateway that did not open it (`known.py`: served from what was kept when it opened) and the pump that sends a bound call's entries down its socket, whichever gateway wrote them, the socket's gateway told on the signal when another binds a call to it (`pump.py`), the WhatsApp threads kept open (`threads.py`: they need the sockets and the session, so they are the gateway's; `api/threads.py` holds their doors), what one gateway sends an app socket another holds (`inbox.py`: a call bound to it, a console's `dev.request`, a stop, the answer back, and the sockets each gateway holds, for the org's list), and who runs each written call and holds each thread across the gateways of the box (`owners.py`: a message that lands on another gateway is handed to the holder; a call it runs is not taken up or reaped elsewhere while it says so), the calls served (`_served.py`); `ending/`: how a call ends (`seal.py`: memory, the bill, the judges, the seal) the calls nobody ends (`reaper.py`), and a call whose worker died (`stranded.py`: LiveKit's word that its agent was lost, the caller told once); `dispatching/`: who takes a call, the gateway's choice and not LiveKit's draw (`offers.py`: a room a caller joined, kept until a worker opens its call; `_chooser.py`: the worker heard lately with the most seats free; `sweep.py`: the offer, another after 12 s, the overflow after three); `api/`: one module per topic of doors, the account doors among them (`accounts.py`: sign-in, whoami, codes, pairing, invitations; `members.py`, `keys.py`, `signup.py`, `sso_login.py`, `org.py`'s provider and mailbox, `ops.py`) | everything above |
| `runner/` | the runner: a world's hosted apps kept running as the gateway wants them (`main.py`), what each beat does to each app decided from numbers alone (`_plan.py`), one gVisor container each, driven through podman (`_podman.py`); no database, no vault | `domain` `wire` `process` |
| `worker/` | the LiveKit worker: the entrypoint and, private, one job per call, the recorder, the traces | `session` `providers` `fleet` `channels` `log` `process` and the leaves |
| `cli/` | `pinecall-runtime`: migrate, doctor, fleet, vault, and `load` (`_load.py`: synthetic calls held against a sandbox through the worker's client, and measured), `facts rebuild` (`_facts.py`), `drift rebuild` (`_drift.py`) | anything |

Three edges are forbidden outright: `gateway` never imports `worker`, `worker` never imports
`gateway`, and nothing imports `gateway/api/`. The leaves hold data and no framework: `domain`
imports only the standard library and pydantic; `wire` only pydantic.
`tests/rules/test_05_import_graph.py` holds this table as a set of edges and fails the commit
that adds one without a diff on it.

## The measures

What each folder weighs, and which folders of ours it reaches for. `tests/rules/test_20_measures.py`
reads this table against the tree on every commit: a folder, its files or its imports out of date
fail it, and so does a line count more than 100 off; the failure prints the table to paste. The
gateway is the largest because its `api/` holds every door of the contract; its private modules,
what the process keeps between doors, have a budget of their own (`test_19_gateway_budget.py`:
2 000 lines together, 600 each), so the platform's side of a call never grows into a second
core under `_` names.

| folder | files | lines | imports of ours |
|---|---|---|---|
| `channels/` | 11 | 2939 | `domain`, `fleet`, `log`, `postgres`, `process`, `tenancy`, `wire` |
| `cli/` | 13 | 2880 | `channels`, `domain`, `fleet`, `gateway`, `log`, `postgres`, `process`, `providers`, `retrieval`, `runner`, `session`, `tenancy`, `wire`, `worker` |
| `domain/` | 8 | 1203 | — |
| `evals/` | 11 | 2965 | `domain`, `log`, `postgres`, `providers`, `session`, `wire` |
| `fleet/` | 6 | 1467 | `domain`, `postgres`, `process`, `wire` |
| `gateway/` | 56 | 12047 | `channels`, `domain`, `evals`, `fleet`, `log`, `postgres`, `process`, `providers`, `retrieval`, `session`, `tenancy`, `wire` |
| `log/` | 14 | 4837 | `domain`, `postgres`, `process`, `wire` |
| `postgres/` | 2 | 283 | `domain` |
| `process/` | 9 | 1849 | `domain`, `postgres` |
| `providers/` | 6 | 1308 | `domain`, `postgres`, `process`, `wire` |
| `retrieval/` | 6 | 2352 | `domain`, `log`, `postgres`, `providers`, `wire` |
| `runner/` | 3 | 866 | `domain`, `process`, `wire` |
| `session/` | 13 | 3615 | `domain`, `log`, `providers`, `wire` |
| `tenancy/` | 38 | 7862 | `domain`, `log`, `postgres`, `process`, `wire` |
| `wire/` | 20 | 5148 | `domain` |
| `worker/` | 4 | 1012 | `channels`, `domain`, `fleet`, `process`, `providers`, `session`, `wire` |

## The path of a call

1. A carrier or a browser reaches LiveKit; LiveKit dispatches the room to the fleet of the
   call's world (`PINECALL_FLEET`), and a worker takes the job.
2. The job asks the gateway to open the call (`POST /v1/calls`): the gateway writes
   `call.ringing` to a new log (an outbound call's first entry, `call.dialing`, was written when
   it was placed), finds the agent's app socket, and answers the minutes left. `call.started` is
   the session's, once the media is up.
3. `session/voice.py` builds the pipeline from the org's providers, `session/session.py`
   starts the `AgentSession` and writes every turn, tool call and metric as an entry. The
   entries travel in batches (`session/call.py`, `Writing`): one task per call sends whatever is
   queued, up to 64, in one `POST /v1/calls/{call}/entries`, and while it is out the next batch
   fills, so an idle call sends each entry alone and a busy one batches itself. Each entry is
   stamped when it is queued. Past 4 096 waiting, an ephemeral entry is shed; a durable one never
   is. A written call's session sends its batches the same way, straight to its log. In the
   gateway every append of every call goes through one writer (`log/_writer.py`) and its two
   lanes, one transaction at a time each, with no timer: an idle append is written at once, and
   what arrives while a transaction is out, up to 500 entries, is the next group (the heads locked
   and read in one statement, moved with the rows in one more, the facts folded once). A log is in
   a group once, so its seqs keep the order its requests came in, and is never written by one lane
   while the other holds an earlier request of it. What carries a fed type (a summary, a score, a
   code, a callback, WhatsApp's queue) goes in the fed lane, which alone takes the feed's lock, so
   no other append ever waits on it. Each request is answered after its commit; a request its own
   entries break is refused alone and the rest of its group is written.
4. A tool call crosses the app socket (`gateway/api/apps.py`) to the tenant's process and back;
   the gateway writes `tool.call` and `tool.result`.
5. At hang-up the job seals the log (`POST /v1/calls/{call}/sealed`): the summary, the price,
   memory and the judges' verdicts are folded from the entries. A call is sealed once: a second
   knock waits for the first, and a seal that broke after its summary goes on from the score.

## What is retried, and what never is

A retry is written only where doing a thing twice changes nothing, or where not doing it loses
something a call cannot get back. The worker's client (`fleet/client.py` `again`) retries while
the gateway is away (unreachable, or a `5xx`), waiting 0.5 s and doubling to 5 s; a `4xx` is an
answer and is never retried.

| what | retried how | why | file |
|---|---|---|---|
| a batch of a call's entries, every one a worker writes (the session's, a refused command's `error`, the `call.ended` of a leg nobody answered, the overflow's sentence and end, the told job's sentence) | without limit, the same batch after the same `after` | a call has one writer from its open to its seal, and the head counts what it took, so a retry is answered with the seqs it was given; an entry dropped is a hole in the log. The told job's writer follows on from the count its dispatch carries | `fleet/client.py` `append_many`, `session/call.py` `Writing`, `worker/_job.py` `writer_of`, `log/store.py` |
| a tool call | while the gateway is away, up to the tool's `timeout_s` (30 s unless declared) plus 5 s | the gateway runs one round trip per call id: a retry joins the one still running, and one already finished (its answer lost, or the gateway restarted) is answered with the `tool.result` the log holds and never sent to the app again, since a tool may book or charge; only a round trip reads the log for it | `fleet/client.py` `tool`, `session/tools.py` `ToolCalls`, `log/queries.py` `tool_answered` |
| the seal | within 30 s | a call is sealed once: the gateway that takes the head's lease seals it, a second knock on any gateway waits for the head to say sealed; a seal that broke gives the lease back and goes on from the score on the next knock | `fleet/client.py` `sealed`, `gateway/ending/seal.py` |
| a call nothing sealed | every 60 s: a spoken call silent 5 min with no agent in its room, a written one this gateway no longer serves quiet 5 min (2 h on WhatsApp) | a worker that dies writes no `call.ended` | `gateway/ending/reaper.py` |
| a page of a call's log | within 30 s | a read | `fleet/client.py` `since` |
| the commands stream | reopened without limit; a `4xx` ends it | commands have no seq: a worker that loses the stream asks again | `worker/_job.py` `_commands` |
| a request for a call no gateway kept the opening of (`404`: opened by an older release) | the call said again (`/reopened`), once, then the request once more | a gateway that restarted serves it from the worker's word | `fleet/client.py` `_on_the_call` |
| the heartbeat | every 5 s, whatever the last one answered | the worker keeps its calls while the gateway is away | `fleet/heartbeat.py` |
| a tool call whose app's socket went | sent again to the socket that takes the call over | the model is still waiting on that call id | `gateway/_served.py`, `session/tools.py` `pending` |
| an embedding | two more tries on a transport error, `408`, `409`, `429` or `5xx`, 0.5 s doubling to 8 s or the server's `retry-after` up to 60 s; a batch too big is split in halves, 5 times at most | an embedding is the same vector however often it is asked | `retrieval/embed.py` |
| a vendor's stream (ears, model, voice) | livekit's own: 3 retries 2 s apart, 10 s each (its defaults, which the session keeps); the session closes after 3 unrecoverable errors of a stage | `408`, `429` and `5xx` pass; a refusal that never succeeds (`400`–`404`, `422`, a WebSocket policy close) ends the call on the first | `session/session.py` `_failed`, `session/_livekit.py` |
| a reader's stream (SSE) | the client reconnects after 1 s and resumes from `Last-Event-ID` | stored before published: nothing is lost between two reads | `gateway/_streams.py` |
| Meta's webhook | Meta delivers again for 7 days | each message id is claimed once per org before it is read | `gateway/calls/threads.py`, `channels/whatsapp.py` |

| never retried | what happens instead | why | file |
|---|---|---|---|
| a dial | a dispatch LiveKit refuses ends the call `dial_failed`; a far end that does not answer ends and seals it; each dial is one row of the ledger, counted against the org's pace and the number's day | a person is never dialled twice by a retry | `channels/telephony/dialing.py`, `worker/_job.py` `_answered`, `tenancy/dial_policy.py` |
| a lookup (recall, search) | the turn waits 250 ms spoken, 3 s written, and goes on without what did not come back | a turn is waiting | `fleet/client.py` `lookup`, `session/tools.py` `Lookups` |
| opening a call | once; a gateway away fails the job | opening writes `call.ringing` and counts the call against the quota | `fleet/client.py` `open` |
| the tenant's app | a tool unanswered by its `timeout_s` is an error the model reads in the same turn; a console's verb waits 120 s | the app's side effects are the app's: the platform never sends a call twice | `session/tools.py` `_awaited`, `gateway/api/relay.py` |
| a reply to a WhatsApp contact | Meta's refusal is an `error` entry `whatsapp_not_sent` on the call | a send whose answer was lost may have reached the contact | `gateway/calls/threads.py` `replied` |
| a judge | a judge whose model broke is skipped and says why; the call seals all the same | the seal never fails on a judge | `evals/judges.py`, `gateway/ending/seal.py` |
| memory at hang-up | one try within 8 s unless the settings say; what fails is logged | memory is a courtesy to the next call | `gateway/ending/seal.py` |
| the overflow's sentence | said once, then a phone caller's call back is written down | the caller hears it once | `worker/main.py` `_said_once` |
| Twilio's API | one request, 30 s; its refusal is its own sentence | an import or a purchase waits for a person, never a call; a purchase twice is two numbers | `channels/telephony/_twilio.py` |
| a statement to Postgres | the door answers `5xx` | the worker's client outlasts it; the gateway holds no retry of its own | `postgres/pool.py` |

## Three hops

A door (or a job) calls one function of the domain, which calls the library. A function that
is not one of those three is a layer and is not written; the commit message of each change
names one operation and its hops.
