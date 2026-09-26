# The patterns, by name

The runtime's design, named: each pattern, the file that embodies it, and the test that fails the
suite the day the tree stops following it. [ARCHITECTURE.md](../ARCHITECTURE.md) is the shape of
the thing section by section; this page is the index of the ideas it is built from, and then one
call walked through every layer. The words are [glossary.md](glossary.md)'s.

| pattern | where | held by |
|---|---|---|
| **Layered packages, a DAG** | ARCHITECTURE.md §11: every package names the packages it may import; `api/` and `worker/` at the top never import each other | `tests/test_isolation.py::test_a_package_imports_only_the_packages_its_line_names` |
| **Shared kernel** | `pinecall-core`: `types/` (the shapes), `extensions/`, `errors/`, on the standard library alone, installed by the runtime and by a policy that never installs the runtime | `test_isolation.py::test_the_core_imports_nothing_of_the_runtime` |
| **Ports and adapters** | a store is `<port>.py` (the `Protocol`, its records, its errors, `<port>_for`), `<port>_memory.py` (the spec by example the unit ring runs on), `<port>_postgres.py` (+ `_sql.py`): `auth/keys*.py`, `orgs/records*.py`, `log/store/` | `tests/test_ports_and_adapters.py` |
| **Factory** | `<port>_for(pool)` picks the adapter: Postgres when the box has a pool, memory when it has none (`orgs/tuning_store.py:tuning_for`) | the same test: an adapter lives in the file named by its store |
| **Dependency injection** | the composition roots: `api/deps.py` (`get_x` hands a door what it needs, `require_x` refuses, `XDep` is the annotation), `api/app.py`'s lifespan, `worker/job.py` | `tests/test_doors_are_controllers.py` (a dependency is where it is named) |
| **Controller and use case** | a door parses, calls one verb, wires the answer (`api/accounts/login.py` → `accounts/signing_in.py`); the verbs live in the domain package they change: `accounts/`, `telephony/`, `orgs/corners.py`, `live/` | `tests/test_doors_are_controllers.py` |
| **Domain errors, mapped once** | a verb raises its own `PinecallError` (`accounts/refusals.py`, `telephony/placing.py`); `api/refusals.py:STATUS_OF` gives each its status, and the sentence travels as the detail | `tests/api/test_refusals.py` |
| **Event sourcing** | the log is the truth: `log/entry.py` (an entry, its `seq` born in the INSERT), `log/reduce.py` (entries folded into a state, the same rules as the TypeScript reducer), `log/snapshots.py` (one reduction per call per seq) | `tests/log/test_the_golden_log.py`: the same golden log reduces to the same state here and in TypeScript |
| **Read models** | what is asked across calls is folded as the log grows, never recomputed: `log/call_facts.py` (a row per call), `log/usage.py`, `orgs/meter.py` | `tests/log/test_call_index.py` |
| **Observer (fan-out)** | `log/fanout.py`: every live reader of one log on a bounded queue; a slow reader is dropped, an append never waits | `tests/log/test_fanout.py` |
| **Registry** | `providers/registry.py:Vendors`: every vendor this build runs, registered by name; `providers/catalog.py` what each does. The only place a vendor is named | `test_isolation.py::test_only_providers_imports_a_vendor_sdk` |
| **Plugin (extension point)** | `extensions/points.py`: a named point the runtime answers itself until a package installed beside it (`PINECALL_EXTENSIONS`, `cloud/`) registers another | `packages/pinecall-core/tests/extensions/test_the_points.py` |
| **Command dispatch** | the app socket's commands, one handler each, registered with `@handles` in `api/agents/handlers.py:HANDLERS`; the worker applies its own in `worker/commands.py` | `tests/api/agents/test_socket.py` |
| **Configuration object** | `settings/schema.py:Settings`: every variable once, read by `load_settings()` alone | `tests/settings/test_example.py` (`.env.example` is what the class declares) |
| **Versioned schema** | `db/migrations/`: numbered, never edited once landed, each landed file's hash in `applied.sha256` | `tests/db/test_migrations.py::test_every_landed_migration_is_the_file_the_databases_that_ran_it_ran` |
| **Package index (facade)** | every package another reads from opens with its `__init__.py` and `__all__`: `from pinecall.accounts import sign_in_with_password` | `tests/test_the_public_surface.py` |

What is **not** here, on purpose: no `Service`, `UseCase` or `Repository` layer. A verb in a domain
package is the service; a port named by its entity (`Orgs`, `Keys`, `Members`) is the repository.

## A call, layer by layer

Where a call is, read from the bottom up: each layer imports only the ones below it.

1. **`types/call.py`** (core) — the shape: `CallContext`, who called whom, on which door, in which world.
2. **`log/`** — its record: every entry appended, folded into its state by `log/reduce.py`, sealed
   by `call.ended`; `log/call_facts.py` its row in every list.
3. **`orgs/`** — what the org set for it: `orgs/corners.py:tuned_for` lays the corner's tuning and
   lexicon on what the app declared, per session, never cached.
4. **`session/`** — running it: `session/text/session.py` (a written call, in the gateway) and
   `session/voice/session.py` (a spoken one, in the worker), the same prompt, tools and judges.
5. **`worker/`** — the process that hosts a spoken call: `worker/job.py`, from the room to the
   sealed log, asking the gateway over HTTP for everything it does not hold.
6. **`live/`** — what this gateway holds open: a text call opened (`live/opening.py`), taken up
   after a restart (`live/resuming.py`), handed to a socket (`live/attaching.py`).
7. **`api/calls/`** — its doors: the log's stream, its state, the worker's writes, the desk.
