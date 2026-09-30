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
| `process/` | what one process is given and holds open: `settings.py` (every variable), `connections.py` (`Connections`: the pool, the vault key, an HTTP client, the LiveKit client; opened once, closed in reverse), what the gateway counts as it works and the Prometheus text it is read as (`metrics.py`), where a call's audio is kept (`recordings.py`: the disk it was recorded on, or the recordings bucket under its org, read with byte ranges and erased there too), and the installation's own configuration kept in Postgres (`box_settings.py`: the rows the operator edits) | `domain` `postgres` |
| `wire/` | every frame, event, command, part, state, metric and the judges' score (`scores.py`) as pydantic models, one file per family; `rest/` holds the doors' bodies per family (`accounts`, `agents`, `calls`, `evals`, `fleet`, `hosting`, `numbers`, `retrieval`) | `domain` |
| `postgres/` | the pool and the migration runner, with `migrations/0001_schema.sql` beside it | `domain` |
| `log/` | a call's log: the store, the facts folded at write (`facts.py`), the queries over them (`queries.py`), the facts folded again from the log (`refold.py`: `facts rebuild` and doctor's check), each day's drift counted at the seal (`drift.py`: the stages' histograms, `_histogram.py`, and the judges' verdicts, by agent and version; `drift rebuild`), the logs open in this process and their live readers (`logs.py`), the reducer, the two read projections | `domain` `wire` `postgres` `process` |
| `tenancy/` | orgs, people, keys, the tokens the gateway signs, the vault, admission (the quotas an org is born with and counted against), per-scope agent tuning (`scopes.py`), the org's carrier accounts (`carriers.py`) and dial policy (the callee's hours, a number's daily count, consent and the do-not-call list among its guards; `consents.py` keeps those facts), erasure (`erasure.py`: a call, a contact or an org deleted through the one path `call_log`'s trigger admits, and its trail), the org's policy (`policy.py`: retention, its calling rules, and what a call says first, in `disclosure.py`) and the nightly retention run (`retention.py`, which also forgets call records past 24 months), a number's traceback (`traceback.py`: its calls kept or erased, and its dials), who read what (`reads.py`: a person's or the operator's read of a call, a recording or a number), the org's world exported as JSON Lines (`export.py`), caller codes, personas, an agent's own judges (`judges.py`), the apps the box hosts for an org with their releases (`hosting.py`), what happens to one while it runs — stopped, its logs, the time it served (`hosted_running.py`) — and the org's secrets (`org_secrets.py`), sign-in (a password, a one-use code, a paired terminal, a sign-up), the org's identity provider, mail and the letters it sends | `domain` `wire` `postgres` `process` `log` |
| `providers/` | the box's providers configuration: the catalog row in `box_settings`, building a LiveKit plugin by name, the keyring a call runs on (`credentials.py`), prices, voices | `domain` `wire` `postgres` `process` |
| `session/` | one LiveKit `AgentSession` for voice and text: `session.py`, a voice call's pipeline (`voice.py`: the three stages built, and the end of the caller's turn read off the audio by a local model, livekit's `v1-mini` or Smart Turn v3 as the row says), a written one (`text.py`), the call's live state (`call.py`), the room, hold music (`hold.py`), tools, the widget channel; private: the Agent, livekit's shapes read as ours, the prompt, what the ears are told (`_hearing.py`: the turn policy per language, the keyterms) | `domain` `wire` `providers` `log` `process` |
| `retrieval/` | the embedder (`embed.py`), knowledge bases with their cutter and goldens (`knowledge.py`), contact memory (`memory.py`), a call's lookups written on its log (`lookups.py`), and what a call taught at hang-up (`extraction.py`); private: the hybrid search over one table (`_search.py`) | `domain` `wire` `postgres` `log` `providers` `process` |
| `evals/` | the case a judge reads (`case.py`), the judges and the hang-up panel (`judges.py`), the compliance judges settled by code (`compliance.py`), the checks by code alone (`checks.py`), a golden played on a written call and the judges its expectations set (`goldens.py`), a run and its matrix (`runs.py`), the simulated caller (`callers.py`) and its spoken line (`spoken.py`); private: what an agent could know and stated (`_evidence.py`) | `session` `retrieval` `log` `providers` and the leaves |
| `channels/` | by where a conversation comes in: `routes.py` (number or channel to agent, for every channel), `rooms.py` (a call to the fleet of its world), `whatsapp.py` (Meta's API), `telephony/` (Twilio's API, the SIP trunks and rules on LiveKit, numbers imported and bought, dialling out) | `domain` `wire` `postgres` `process` `tenancy` `fleet` `log` |
| `fleet/` | the fleet each world dispatches to (`worlds.py`), the roster of workers, a worker's heartbeat, the worker's client to the gateway | `domain` `wire` `postgres` `log` `process` |
| `gateway/` | the FastAPI app; private, each named for what it holds and none the logic of a door of the same name: the process's state (`_gateway.py`, the `Gateway`), each request's dependencies (`_deps.py`), what a call of an agent is set up with (`_call_setup.py`), written calls opened and taken up (`_text_calls.py`), SSE streams, the app sockets registered (`_sockets.py`), the calls served (`_served.py`), the WhatsApp threads kept open (`_threads.py`: they need the sockets and the session, so they are the gateway's; `api/threads.py` holds their doors); `ending/`: how a call ends (`seal.py`: memory, the bill, the judges, the seal) the calls nobody ends (`reaper.py`), and a call whose worker died (`stranded.py`: LiveKit's word that its agent was lost, the caller told once); `api/`: one module per topic of doors, the account doors among them (`accounts.py`: sign-in, whoami, codes, pairing, invitations; `members.py`, `keys.py`, `signup.py`, `sso_login.py`, `org.py`'s provider and mailbox, `ops.py`) | everything above |
| `runner/` | the runner: a world's hosted apps kept running as the gateway wants them (`main.py`), one gVisor container each, driven through podman (`_podman.py`); no database, no vault | `domain` `wire` `process` |
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
| `channels/` | 7 | 2250 | `domain`, `fleet`, `log`, `postgres`, `process`, `tenancy`, `wire` |
| `cli/` | 9 | 2185 | `channels`, `domain`, `fleet`, `gateway`, `log`, `postgres`, `process`, `providers`, `retrieval`, `runner`, `session`, `tenancy`, `wire`, `worker` |
| `domain/` | 8 | 1175 | — |
| `evals/` | 9 | 2634 | `domain`, `log`, `postgres`, `providers`, `session`, `wire` |
| `fleet/` | 5 | 938 | `domain`, `postgres`, `process`, `wire` |
| `gateway/` | 45 | 10182 | `channels`, `domain`, `evals`, `fleet`, `log`, `postgres`, `process`, `providers`, `retrieval`, `session`, `tenancy`, `wire` |
| `log/` | 9 | 3471 | `domain`, `postgres`, `wire` |
| `postgres/` | 2 | 229 | `domain` |
| `process/` | 5 | 796 | `domain`, `postgres` |
| `providers/` | 6 | 1271 | `domain`, `postgres`, `process`, `wire` |
| `retrieval/` | 6 | 2351 | `domain`, `log`, `postgres`, `providers`, `wire` |
| `runner/` | 2 | 554 | `domain`, `process`, `wire` |
| `session/` | 12 | 3258 | `domain`, `log`, `providers`, `wire` |
| `tenancy/` | 29 | 6770 | `domain`, `log`, `postgres`, `process`, `wire` |
| `wire/` | 19 | 4717 | `domain` |
| `worker/` | 4 | 990 | `channels`, `domain`, `fleet`, `process`, `providers`, `session`, `wire` |

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
   is. A written call's session sends its batches the same way, straight to its log.
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
| a batch of a call's entries | without limit, the same batch after the same `after` | the head counts what it took, so a retry is answered with the seqs it was given; an entry dropped is a hole in the log | `fleet/client.py` `append_many`, `log/store.py` |
| one entry (`call.ended`, `error`, the overflow's transcript the job writes itself) | without limit | a hole in the log is worse than a repeat, and this door is not written once: an answer lost after the write writes the entry again | `fleet/client.py` `append` |
| a tool call | while the gateway is away, up to the tool's `timeout_s` (30 s unless declared) plus 5 s | the gateway runs one round trip per call id and a retry joins the one still running; one already finished is not remembered, and runs again | `fleet/client.py` `tool`, `session/tools.py` `ToolCalls` |
| the seal | within 30 s | a call is sealed once: a second knock waits for the first; a seal that broke after its summary goes on from the score | `fleet/client.py` `sealed`, `gateway/ending/seal.py` |
| a call nothing sealed | every 60 s: a spoken call silent 5 min with no agent in its room, a written one this gateway no longer serves quiet 5 min (2 h on WhatsApp) | a worker that dies writes no `call.ended` | `gateway/ending/reaper.py` |
| a page of a call's log | within 30 s | a read | `fleet/client.py` `since` |
| the commands stream | reopened without limit; a `4xx` ends it | commands have no seq: a worker that loses the stream asks again | `worker/_job.py` `_commands` |
| a request for a call the gateway forgot (`404`) | the call said again (`/reopened`), once, then the request once more | a gateway that restarted serves it from the worker's word | `fleet/client.py` `_on_the_call` |
| the heartbeat | every 5 s, whatever the last one answered | the worker keeps its calls while the gateway is away | `fleet/heartbeat.py` |
| a tool call whose app's socket went | sent again to the socket that takes the call over | the model is still waiting on that call id | `gateway/_served.py`, `session/tools.py` `pending` |
| an embedding | two more tries on a transport error, `408`, `409`, `429` or `5xx`, 0.5 s doubling to 8 s or the server's `retry-after` up to 60 s; a batch too big is split in halves, 5 times at most | an embedding is the same vector however often it is asked | `retrieval/embed.py` |
| a vendor's stream (ears, model, voice) | livekit's own: 3 retries 2 s apart, 10 s each (its defaults, which the session keeps); the session closes after 3 unrecoverable errors of a stage | `408`, `429` and `5xx` pass; a refusal that never succeeds (`400`–`404`, `422`, a WebSocket policy close) ends the call on the first | `session/session.py` `_failed`, `session/_livekit.py` |
| a reader's stream (SSE) | the client reconnects after 1 s and resumes from `Last-Event-ID` | stored before published: nothing is lost between two reads | `gateway/_streams.py` |
| Meta's webhook | Meta delivers again for 7 days | each message id is claimed once per org before it is read | `gateway/_threads.py`, `channels/whatsapp.py` |
| a recording's file | asked every 0.2 s for 8 s after the call | egress finishes writing after the call, and the summary points at the file | `worker/_recorder.py` |

| never retried | what happens instead | why | file |
|---|---|---|---|
| a dial | a dispatch LiveKit refuses ends the call `dial_failed`; a far end that does not answer ends and seals it; each dial is one row of the ledger, counted against the org's pace and the number's day | a person is never dialled twice by a retry | `channels/telephony/dialing.py`, `worker/_job.py` `_answered`, `tenancy/dial_policy.py` |
| a lookup (recall, search) | the turn waits 250 ms spoken, 3 s written, and goes on without what did not come back | a turn is waiting | `fleet/client.py` `lookup`, `session/tools.py` `Lookups` |
| opening a call | once; a gateway away fails the job | opening writes `call.ringing` and counts the call against the quota | `fleet/client.py` `open` |
| the tenant's app | a tool unanswered by its `timeout_s` is an error the model reads in the same turn; a console's verb waits 120 s | the app's side effects are the app's: the platform never sends a call twice | `session/tools.py` `_awaited`, `gateway/api/relay.py` |
| a reply to a WhatsApp contact | Meta's refusal is an `error` entry `whatsapp_not_sent` on the call | a send whose answer was lost may have reached the contact | `gateway/_threads.py` `replied` |
| a judge | a judge whose model broke is skipped and says why; the call seals all the same | the seal never fails on a judge | `evals/judges.py`, `gateway/ending/seal.py` |
| memory at hang-up | one try within 8 s unless the settings say; what fails is logged | memory is a courtesy to the next call | `gateway/ending/seal.py` |
| the overflow's sentence | said once, then a phone caller's call back is written down | the caller hears it once | `worker/main.py` `_said_once` |
| Twilio's API | one request, 30 s; its refusal is its own sentence | an import or a purchase waits for a person, never a call; a purchase twice is two numbers | `channels/telephony/twilio.py` |
| a statement to Postgres | the door answers `5xx` | the worker's client outlasts it; the gateway holds no retry of its own | `postgres/pool.py` |

## Three hops

A door (or a job) calls one function of the domain, which calls the library. A function that
is not one of those three is a layer and is not written; the commit message of each change
names one operation and its hops.
