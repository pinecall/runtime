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
| `process/` | what one process is given and holds open: `settings.py` (every variable), `connections.py` (`Connections`: the pool, the vault key, an HTTP client, the LiveKit client; opened once, closed in reverse), and the installation's own configuration kept in Postgres (`box_settings.py`: the rows the operator edits) | `domain` `postgres` |
| `wire/` | every frame, event, command, part, state, metric as pydantic models, one file per family; `rest/` holds the doors' bodies per family (`accounts`, `agents`, `calls`, `evals`, `fleet`, `numbers`, `retrieval`) | `domain` |
| `postgres/` | the pool and the migration runner, with `migrations/0001_schema.sql` beside it | `domain` |
| `log/` | a call's log: the store, the facts folded at write (`facts.py`), the queries over them (`queries.py`), the logs open in this process and their live readers (`logs.py`), the reducer, the two read projections | `domain` `wire` `postgres` `process` |
| `tenancy/` | orgs, people, keys, the tokens the gateway signs, the vault, admission (the quotas an org is born with and counted against), per-scope agent tuning (`scopes.py`), the org's carrier accounts (`carriers.py`) and dial policy, caller codes, personas, sign-in (a password, a one-use code, a paired terminal, a sign-up), the org's identity provider, mail and the letters it sends | `domain` `wire` `postgres` `process` `log` |
| `providers/` | the box's providers configuration: the catalog row in `box_settings`, building a LiveKit plugin by name, the keyring a call runs on (`credentials.py`), prices, voices | `domain` `wire` `postgres` `process` |
| `session/` | one LiveKit `AgentSession` for voice and text: `session.py`, a voice call's pipeline (`voice.py`), a written one (`text.py`), the call's live state (`call.py`), the room, hold music (`hold.py`), tools, the widget channel; private: the Agent, livekit's shapes read as ours, the prompt, what the ears are told (`_hearing.py`: the turn policy per language, the keyterms) | `domain` `wire` `providers` `log` `process` |
| `retrieval/` | the embedder (`embed.py`), the hybrid search over one table (`search.py`), knowledge bases with their cutter and goldens (`knowledge.py`), contact memory (`memory.py`), and what a call taught at hang-up (`extraction.py`) | `domain` `wire` `postgres` `log` `providers` `process` |
| `evals/` | the case a judge reads (`case.py`), the judges and the hang-up panel (`judges.py`), the checks by code alone (`checks.py`), a golden played on a written call (`goldens.py`), a run and its matrix (`runs.py`), the simulated caller (`callers.py`) and its spoken line (`spoken.py`); private: what an agent could know and stated (`_evidence.py`) | `session` `retrieval` `log` `providers` and the leaves |
| `channels/` | by where a conversation comes in: `routes.py` (number or channel to agent, for every channel), `rooms.py` (a call to the fleet of its world), `whatsapp.py` (Meta's API), `telephony/` (Twilio's API, the SIP trunks and rules on LiveKit, numbers imported and bought, dialling out) | `domain` `wire` `postgres` `process` `tenancy` `fleet` `log` |
| `fleet/` | the fleet each world dispatches to (`worlds.py`), the roster of workers, a worker's heartbeat, the worker's client to the gateway | `domain` `wire` `postgres` `log` `process` |
| `gateway/` | the FastAPI app; private, each named for what it holds and none the logic of a door of the same name: the process's state (`_gateway.py`, the `Gateway`), each request's dependencies (`_deps.py`), what a call of an agent is set up with (`_call_setup.py`), written calls opened and taken up (`_text_calls.py`), SSE streams, the app sockets registered (`_sockets.py`), the calls served (`_served.py`), the WhatsApp threads kept open (`_threads.py`: they need the sockets and the session, so they are the gateway's; `api/threads.py` holds their doors); `api/`: one module per topic of doors, the account doors among them (`accounts.py`: sign-in, whoami, codes, pairing, invitations; `members.py`, `keys.py`, `signup.py`, `sso_login.py`, `org.py`'s provider and mailbox, `ops.py`) | everything above |
| `worker/` | the LiveKit worker: the entrypoint and, private, one job per call, the recorder, the traces | `session` `providers` `fleet` `channels` `log` `process` and the leaves |
| `cli/` | `pinecall-runtime`: migrate, doctor, fleet, vault | anything |

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
| `channels/` | 7 | 2111 | `domain`, `fleet`, `log`, `postgres`, `process`, `tenancy`, `wire` |
| `cli/` | 1 | 198 | `domain`, `gateway`, `postgres`, `process`, `tenancy`, `worker` |
| `domain/` | 8 | 1117 | — |
| `evals/` | 8 | 2225 | `domain`, `log`, `postgres`, `providers`, `session`, `wire` |
| `fleet/` | 4 | 642 | `domain`, `postgres`, `process`, `wire` |
| `gateway/` | 32 | 6812 | `channels`, `domain`, `evals`, `fleet`, `log`, `postgres`, `process`, `providers`, `retrieval`, `session`, `tenancy`, `wire` |
| `log/` | 6 | 2308 | `domain`, `postgres`, `wire` |
| `postgres/` | 2 | 229 | `domain` |
| `process/` | 3 | 438 | `domain`, `postgres` |
| `providers/` | 6 | 952 | `domain`, `postgres`, `process`, `wire` |
| `retrieval/` | 5 | 2140 | `domain`, `postgres`, `providers`, `wire` |
| `session/` | 12 | 2968 | `domain`, `log`, `providers`, `wire` |
| `tenancy/` | 16 | 4749 | `domain`, `log`, `postgres`, `process`, `wire` |
| `wire/` | 13 | 3563 | `domain` |
| `worker/` | 4 | 906 | `channels`, `domain`, `fleet`, `process`, `providers`, `session`, `wire` |

## The path of a call

1. A carrier or a browser reaches LiveKit; LiveKit dispatches the room to the fleet of the
   call's world (`PINECALL_FLEET`), and a worker takes the job.
2. The job asks the gateway to open the call (`POST /v1/calls`): the gateway writes
   `call.started` to a new log, finds the agent's app socket, and answers the minutes left.
3. `session/voice.py` builds the pipeline from the org's providers, `session/session.py`
   starts the `AgentSession` and writes every turn, tool call and metric as an entry.
4. A tool call crosses the app socket (`gateway/api/apps.py`) to the tenant's process and back;
   the gateway writes `tool.call` and `tool.result`.
5. At hang-up the job seals the log (`POST /v1/calls/{call}/sealed`): the summary, the price,
   memory and the judges' verdicts are folded from the entries.

## Three hops

A door (or a job) calls one function of the domain, which calls the library. A function that
is not one of those three is a layer and is not written; the commit message of each change
names one operation and its hops.
