# The log

Why the log layer is shaped the way it is. The code is `src/pinecall/log/`; this
page covers the entry and the store contract, and grows as the log lands (fanout, replay,
filters, PII, Postgres). The references, `sdk-server`'s call log and convo's event log, were
read once for what they taught and closed; nothing here is theirs.

## The entry is the envelope

`log/entry.py` re-exports the generated `protocol.envelope.Entry` instead of declaring a
dataclass twin. The choice was open: the layout allows `log/` to import `protocol/`, since
neither names a framework, and the store hands back what the wire streams.

A twin would have cost a conversion at every append and every read, and given the repo two
shapes for one thing with a test to keep them equal. Re-using the generated model costs a
pydantic validation per append, microseconds, and buys the thing the design promises: what
the store keeps and what the gateway streams are the same bytes. `decode_entry` builds one
from a Postgres row with no conversion; `encode` serialises one for SSE; the reducer already
takes it. `entry.py` adds the log's own vocabulary on top: `ephemeral_by_default(type)` reads
the registry, so a caller that does not say knows what the protocol says.

One caveat, stated so nobody trips on it: a pydantic model is not frozen, and the memory
store hands back the very objects it keeps. A caller that mutates an entry corrupts a test,
never production, where every read rebuilds the model from a row.

## The store contract

`Store` is a `typing.Protocol`, async, six verbs, one refusal.

**`append(call, agent, type, data, ephemeral)` is the only place a seq is born.** The
argument list is the envelope minus the two fields only the store may set: `seq` and `ts`.
The seq race, in one sentence: when the thing that numbers an entry and the thing that writes
it are two, they can disagree, and a reader gets two truths; when a caller numbers and a store
writes, a crash between the two leaves a hole nobody can explain. So the store numbers, under
its own lock, and returns the entry with the number in it. The memory store makes this
structural: the counter read and the write happen with no `await` between them, and a lock
says so, so the next edit cannot slip one in.

**The agent's own log is `call=None`.** Registered, configured, an error outside any call, one
line per call the agent handled: it has a seq of its own and is read with `agent_since`. The
card sketched `append(call, type, data, ephemeral)`; the envelope requires `agent`, and a
store that guessed it would be a store with a second table of who owns what. The caller says
which agent every time; Postgres wants it in the row anyway.

**`since(call, after, limit)` is strictly above the cursor**, in seq order, at most `limit`.
The cursor is the whole replay protocol: a reader reconnects with the last seq it saw and
never gets it again. Ephemeral entries may be missing from what comes back, because a store
may drop them; the memory store keeps them so a test reads back exactly what it wrote, and
says so in its docstring. Postgres will not write them.

**`seal(call)` ends a log.** Every later append is refused with `LogSealed`, a
`PinecallError`. Sealing twice is still sealed, and a call nobody wrote to can be sealed,
because a recovery path may reach it in any order. The terminal entry, `call.summary`, is the
log layer's to write before it seals; the store does not know which type is last.

**The gap without a snapshot** is what the references taught most clearly, and it is not the
store's problem in v2: a durable entry in Postgres is never forgotten, so the only stretch a
reader can miss there is ephemerals, by design. The gap is born in the hot window of the
in-process log, when a slow reader's cursor falls behind what the window still holds; the log
card writes that marker, with the reduced state as a snapshot when it has one, and never
hands back a truncated list as if it were whole.

`list_calls(agent)` is oldest first, `latest_seq(call)` is 0 for a call nobody wrote to, and
`DEFAULT_LIMIT` is 500 because a reader replays in pages and then goes live.

## The Postgres store, and the one door to IO

`log/store/postgres.py` is the only file under `log/` that names a driver, and
`tests/test_isolation.py` now carries exactly one exception saying so: `asyncpg` may be imported
under `log/store/`, nowhere else, and no other framework may be imported there either. A second
test asserts the exception from the other side — the driver *is* under `log/store/` — so the day
the store stops holding it, the excuse for the exception fails out loud instead of rotting. The
rule the exception protects is the one the layout promises: the log's rules never see a
connection. `log.py`, `fanout.py`, `replay.py`, `filters.py` and `pii.py` are written against the
`Store` Protocol and would not notice if the rows lived in a file.

### The seq is born under the database, in one statement

Two shapes were on the table: a per-call counter row bumped with `UPDATE … RETURNING`, or an
advisory lock around `INSERT … SELECT max(seq)+1`. The counter row won, and the reason is not
speed. An advisory lock is a promise every future writer has to keep; whoever writes the second
insert path a year from now has to know the lock exists and remember to take it. The counter row
is not a promise, it is a fact: `call_log_head` has one row per log, and there is no way to get a
number out of it except by taking its row lock. `APPEND` is one statement — the head row is
inserted-or-bumped in a CTE and the entry is written from the number that comes out — so the
number and the row are one transaction, and there is no window in which a seq exists and its
entry does not. `tests/log/test_store.py` proves contiguity with `asyncio.gather` of 50 appends,
against both stores; `test_sigkill.py` proves the other half, that a writer killed mid-loop leaves
`latest_seq == len(rows)` and no hole.

The sealed check rides in the same statement. `ON CONFLICT DO UPDATE … WHERE NOT head.sealed`
updates nothing when the log has ended, returns no row, and that empty result *is* the refusal.
The store never asks "is this sealed?" and then acts on the answer: there is no gap between the
question and the write for a `call.summary` to slip through.

### One table, and `call` stays null

The agent's own log is the same table with `call` null, exactly as the envelope has it. A null
cannot sit in a primary key, so the table carries `log`, a stored generated column the database
computes as `coalesce(call, '@' || agent)`, and the primary key is `(log, seq)`. The identity is
computed by the database rather than passed in, so a row and its head row cannot disagree about
which log they belong to, and a `CHECK` forbids a call id opening with `@` so no call can wear an
agent's name. A sibling table was the alternative and was refused for the obvious reason: every
read verb would have had to be written twice, and `since` and `agent_since` are the same query.

`call_log_head` also carries `agent`, `call` and `started_at`, which is what makes `list_calls`
answerable without reading a single entry — and what makes it answer for a call whose every entry
was ephemeral, which a query over `call_log` could not.

### Ephemerals spend a seq and leave no row

This is the one behaviour where the two stores differ, and it is stated rather than hidden.
Postgres numbers an ephemeral append, hands the caller its `Entry`, and writes nothing: the
durable log then has holes exactly where the interim transcripts were. `since()` returns only
durable rows; `latest_seq()` counts everything handed out, because a reader's cursor is a
position in the seq space, not a row count. `MemoryStore` keeps them, so a test can read back
exactly what it wrote. `tests/log/test_store.py` asserts what both share — one seq space, in
order — and `tests/log/test_postgres.py` asserts the hole.

The golden fixture is replayed through Postgres for the same reason: all 124 entries in,
ephemerals included, and what comes back reduces to the same state as the durable half of what
went in. Two things about that comparison, both in the test's docstring. It is against the durable
half because the contract lets a store forget an ephemeral. And the expected side is renumbered by
position, because the golden is a *reader's stream*, not a store's table: it declares a gap at
39–40, so its seq 39 is an entry the reader never saw, and its last marker repeats seq 124. A
store numbers what it is given, one after another. Everything else must survive byte for byte, and
the reducer is what proves it.

### `ts` comes from the runtime's clock, not the database's

`append` stamps `ts` from a clock the store is constructed with, defaulting to `time.time`, the
same way `MemoryStore` takes one. `now()` in the statement was tempting and is wrong twice over:
the entry's `ts` is when the runtime saw the thing happen, and the gateway is not the database's
machine; and a store whose clock cannot be handed to it cannot replay a fixture, because
`participant.joined` reads `joined_at` off the entry.

### UPDATE and DELETE are refused by the table itself

Two statement-level triggers raise on `call_log`. Statement-level, not row-level, so a `DELETE`
whose `WHERE` matched nothing is refused too: the refusal is about the intent. A log a reader
replayed yesterday must read the same today, or the cursor is not a promise. This is also why the
test suite does not clean up by deleting rows — it cannot. Each pytest process applies the
migrations into a schema of its own, `pinecall_test_<pid>`, and drops it whole at the end; `DROP
SCHEMA` is DDL and the triggers do not speak to it. That is why `PostgresStore.connect` and
`apply_migrations` take a `schema`, checked against a lowercase-word pattern because an identifier
cannot be a parameter.

### Migrations are numbered SQL, applied by the CLI

`pinecall/migrations/*.sql`, plain, numbered, never edited once shipped. They sit at the root of
the distribution, not under `log/store/`, because they are the RUNTIME's: `0001_call_log.sql` is
the log's two tables and `0002_api_keys.sql` is auth's key table, and one schema applied by one
runner is the whole point of having a runner. The store keeps what is genuinely its own — the
driver, and `apply_migrations`, the only code in the tree that talks to asyncpg. DDL lives in a
numbered file from now on; a table's module names its columns in SQL, never in a string constant.

`pinecall-runtime migrate` applies whatever a `schema_migrations` table has not recorded, each file and its record
in one transaction, so half a migration is never remembered as a whole one. `migrate status`
prints what the distribution ships without touching a database. The verb is idempotent by
construction, which is the only property that matters: it runs on every deploy.

## Reading a log while it is written

`CallLog(store, agent, call)` is the writer. It masks, appends through the store, publishes to the
fanout, and — after `call.summary`, the type the protocol makes terminal — seals. The order is the
promise: **the row exists before any reader hears about it**, so a reader can never hold a cursor
the store cannot answer. `AgentLog` is the same minus the seal, because an agent's log has no end
while the agent exists.

### The fanout drops, it never waits

Every live reader has a queue of 256 entries. `publish()` never awaits: a reader that cannot take
the entry is marked dropped, told (its iteration ends after it has read what it was given), and
let go, all inside the same synchronous call. 256 is about a minute of a busy call — long enough
for a browser to render a burst, short enough that a dead socket cannot pin a call's worth of
memory. The queue is allocated one slot deeper than the depth, and that slot is kept for the end
sentinel, so closing a full queue does not block either: a reader that never learns the stream
ended is a reader that hangs. `tests/log/test_fanout.py` times 5,000 publishes with nobody reading
and asserts the burst takes under a second — three orders of magnitude of slack, because it is a
wall against blocking and not a benchmark.

### The gap carries a state, or it is a lie

`replay.stream()` subscribes *before* it reads the first page, so an entry written during the
backlog lands in the queue instead of falling between the two halves; the live half then skips
whatever the backlog already sent, and the cursor decides that, not luck. At the boundary it emits
`log.caught_up` at the last seq sent. When the reader falls behind the hot window and the fanout
drops it, the stream does not quietly resume and it does not hand back a truncated list as if it
were whole — that is precisely what the references got wrong. It emits `log.gap` with `from_seq`,
`to_seq` and a `snapshot`: `reduce()` of everything the store has. One entry catches the reader up
instead of the four hundred it missed, and then the stream re-subscribes and says `log.caught_up`
again. A marker stands at the seq of the last entry it speaks for, per `docs/protocol/README.md`,
and neither marker is ever appended: they are facts about the stream, not about the call.

### Filters narrow what a reader sees, never whether it learns the log ended

`types=` takes at most 32 names over `[a-z0-9_.]` — past that a filter is not a filter, and a
query string that long came from a machine — and `durable=1` drops what a store may forget.
`{log.gap, log.caught_up, call.ended, call.summary}` always pass, whatever was asked for: two say
the stream itself lost something and two say the call is over, and a reader that filtered those
out would wait forever for an entry that came and went.

### PII: the state teaches, everything else pays

`pii.py` masks with `***`, the marker `docs/protocol/projections.md` names, and it does two
different jobs.

By **declared name**: a tool argument the agent listed in `ToolSpec.pii` is masked before the entry
is written, which is exactly what the projections page promises when it says such an argument
"needs nothing here". Masking is by the tool's *own* declaration, not by the state's field names,
so a tool whose argument happens to share a name with a state field is left alone.

By **learned value**: `state.changed` is the one entry the masker does not mask — it *learns* from
it. The tenant's console reads `app_state`, and the tenant projection masks it at the sink, where
the token says who is reading; masking it on the way in would blind the tenant to their own state
and make the projections page a lie. What the masker takes from it is the leaf strings under every
field declared `pii` — a patient object teaches its id, its name and its phone — and from then on
those values are masked wherever they turn up, under anybody's key, longest first so a surname is
not left standing next to a mask. A value shorter than three characters is never learned: it would
mask the alphabet.

And nothing at all happens to `turn.*`, `metrics.*` or the interim transcripts. That is the
mistake the references made and the reason this is a constant in the file and not a habit in
somebody's head: a masked transcript is not a transcript, and a metric is a number nobody's name
can hide in.
