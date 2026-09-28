# Architecture — what each folder does, and what it may import

One package, `pinecall/`, folders by concept, two levels deep at most. Every fact of a call is
an entry appended to the call's log, and everything else (the app socket, the console, the
reducer, the usage feed) reads entries. The media side is LiveKit's `AgentSession`, used as is.

A module another package imports has a plain name; a module only its own package uses starts
with `_` (`gateway/_deps.py`, `tenancy/_signin.py`), as in the Anthropic and OpenAI SDKs. A rule
holds both directions: a `_module` is never imported from outside, and a plain one is imported
by at least one other package. The doors under `gateway/api/` are the exception, because their
surface is the URL.

| folder | what it holds | imports of ours |
|---|---|---|
| `domain/` | the words (`names.py`), an agent's declaration (`agent.py`), a call (`call.py`), an org (`org.py`), people and keys (`person.py`), the scope a request acts in (`scope.py`), the E.164 country codes (`country_codes.py`), the errors with their HTTP status (`errors.py`); no IO | nothing |
| `process/` | what one process is given and holds open: `settings.py` (every variable) and `connections.py` (`Connections`: the pool, the vault key, an HTTP client, the LiveKit client; opened once, closed in reverse) | `domain` `postgres` |
| `wire/` | every frame, event, command, part, state, metric as pydantic models, one file per family; `rest/` holds the doors' bodies per family (`calls`, `agents`, `numbers`, `fleet`, `retrieval`) | `domain` |
| `postgres/` | the pool, the migration runner with `migrations/0001_schema.sql` beside it, and the `box_settings` rows the operator configures (`box_settings.py`) | `domain` |
| `log/` | a call's log: the store, the facts folded at write (`facts.py`), the queries over them (`queries.py`), the logs open in this process and their live readers (`logs.py`), the reducer, the two read projections | `domain` `wire` `postgres` `process` |
| `tenancy/` | orgs, people, keys, the tokens the gateway signs, the vault, admission (the quotas an org is born with and counted against), per-scope agent tuning (`scopes.py`), the org's carrier accounts (`carriers.py`) and dial policy, caller codes, personas; sign-in, SSO, mail and letters are private to it until their doors land | `domain` `wire` `postgres` `process` `log` |
| `providers/` | the box's providers configuration: the catalog row in `box_settings`, building a LiveKit plugin by name, the keyring a call runs on (`credentials.py`), prices, voices | `domain` `wire` `postgres` `process` |
| `session/` | one LiveKit `AgentSession` for voice and text: `session.py`, a voice call's pipeline (`voice.py`), a written one (`text.py`), the call's live state (`call.py`), the room, hold music (`hold.py`), tools, the widget channel; private: the Agent, livekit's shapes read as ours, the prompt, what the ears are told (`_hearing.py`: the turn policy per language, the keyterms) | `domain` `wire` `providers` `log` `process` |
| `retrieval/` | the embedder and hybrid search over knowledge and memory | `domain` `wire` `postgres` `log` `providers` `process` |
| `evals/` | judges, goldens, simulated callers | `session` `retrieval` `log` `providers` and the leaves |
| `channels/` | by where a conversation comes in: `routes.py` (number or channel to agent, for every channel), `rooms.py` (a call to the fleet of its world), `whatsapp.py` (Meta's API), `telephony/` (Twilio's API, the SIP trunks and rules on LiveKit, numbers imported and bought, dialling out) | `domain` `wire` `postgres` `process` `tenancy` `fleet` `log` |
| `fleet/` | the fleet each world dispatches to (`worlds.py`), the roster of workers, a worker's heartbeat, the worker's client to the gateway | `domain` `wire` `postgres` `log` `process` |
| `gateway/` | the FastAPI app; private: the process's state (`_state.py`), each request's dependencies, an agent as a call runs it (`_agents.py`), written calls opened and taken up (`_text_calls.py`), SSE streams, the app sockets registered (`_sockets.py`), the calls served (`_served.py`), the WhatsApp threads kept open (`_threads.py`: they need the sockets and the session, so they are the gateway's; `api/threads.py` holds their doors); `api/`: one module per topic of doors | everything above |
| `worker/` | the LiveKit worker: the entrypoint and, private, one job per call, the recorder, the traces | `session` `providers` `fleet` `channels` `log` `process` and the leaves |
| `cli/` | `pinecall-runtime`: migrate, doctor, fleet, vault | anything |

Three edges are forbidden outright: `gateway` never imports `worker`, `worker` never imports
`gateway`, and nothing imports `gateway/api/`. The leaves hold data and no framework: `domain`
imports only the standard library and pydantic; `wire` only pydantic.
`tests/rules/test_05_import_graph.py` holds this table as a set of edges and fails the commit
that adds one without a diff on it.

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
