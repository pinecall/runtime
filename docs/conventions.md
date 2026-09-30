# Conventions — how a file of this runtime is written

The architecture rules under `tests/rules/` fail the commit. These are the choices a rule
cannot check, and the reader is owed them in one place.

## Modules

- A module another package imports has a plain name. A module only its own package uses starts
  with `_`. Rule 17 holds both: nothing outside imports a `_module`, and something outside
  imports every plain one (the doors of `gateway/api/` excepted: their surface is the URL).
- No module of the package is over 700 lines (rule 15). What grows past it is two concerns.
- A folder holds at least two files; a concept with one file is a file, not a folder.

## Names

- A door's body is `<Verb><Noun>Request`; what it answers is `<Verb><Noun>Response`; the query
  of a listing is `<Noun>Query`: `OpenCallRequest`, `OpenCallResponse`, `CallListQuery`.
- An event is a fact in the past tense: `CallStarted`, `ToolCalled`. A command is an imperative:
  `CallDial`, `AgentSay`.
- A door function is `<verb>_<noun>`: `open_call`, `stream_org_events`, `mint_room_token`. Its
  parameters are `body`, `query`, `key`, `scope`, `app`, `reader`.
- A variable is the noun of what it holds: `entry`, `account`, `number`, `row`. A boolean reads
  as a question: `is_sealed`. Rule 16 refuses `said`, `held`, `told`, `one`, `looked`, `asked`,
  `stood`, `stands`, `standing`, `keyed`, `whose` and any name starting with `the_`, and a class
  ending in `Asked`, `Told`, `Looked`, `Wanted` or `Standing`. A data field keeps the wire's or the
  table's word, whatever it is.
- A domain word is used only if `glossary.md` defines it.
- A module is named for its concept, never a role: no `utils`, `helpers`, `base`, `manager`,
  `service`, `handler`, `factory`, `provider`, `adapter`.

## Data

- `@dataclass(frozen=True)` for what the runtime builds and passes around itself: a row read
  from Postgres, a plan, a scope, a claim.
- A pydantic model for what crosses a boundary and is validated there: the wire, a door's body,
  a sealed credential, a row of `box_settings`. The wire's models refuse unknown keys.
- A `Literal` type is trusted once inside: the wire and the schema's `CHECK` enforce it; a
  `parse_*` refuses at the edge (CLI, a column).
- No class with one public method: it is a function. A class only where state outlives one
  call: a store with a pool, a session, the live registry.

## Functions

- Five parameters at most, keyword-only after the second; a sixth is a dataclass. No boolean
  that selects a behaviour: two functions.
- Return early; nesting three deep at most; no `else` after `return`.
- A behaviour a command needs (voice only, a live room) is a `ClassVar` on the command's model,
  refused in one place in the session; never a chain of `isinstance` in a dispatcher. A kind
  of account lists its own numbers; the pydantic union dispatches.

## Errors

- Raise a class of `domain/errors.py` with the sentence a person reads; the class carries the
  HTTP status and `gateway/app.py` has the one handler. `HTTPException` is never raised by hand.
- Never a bare `except`; a caught exception is re-raised as ours or logged with
  `exc_info=True` and the reason the call goes on.

## The database

- The pool's connections are autocommit: a statement alone is one round trip, with no `BEGIN`
  and no `COMMIT` around it. Statements that must land together, and any statement that only
  means something inside a transaction (`FOR UPDATE`, an advisory `xact` lock, `SET LOCAL`), open
  `connection.transaction()`.
- A `pool.connection()` block that runs more than one statement outside a transaction says why
  they are independent, on the line above it: `# independent: <why>` (rule 23).
- A block holds its connection only while it talks to the database: an HTTP call, a vendor, a
  sleep or a stream to a client happens before the block or after it. What is long on purpose
  runs in `unbounded(pool)`, which is a transaction with no timeout.

## Tasks and time

- Every task has an owner. `asyncio.create_task` appears only in the files
  `tests/rules/allowed.py` names, each with who cancels or awaits the task (rule 14).
- Doors read time from the log's clock, so a test's clock and the window checks agree; no test
  sleeps real time.

## Comments and docstrings

- One-line docstring on every module, public function and class: one sentence about behaviour,
  no `Args:`/`Returns:` sections. The signature with its types is the argument list.
- A comment says only what the code cannot: an external fact, a trap, a reason. One line.
- A module docstring never defines the word with the word.

## Deprecating something on the wire

The first deprecation sets the pattern: the field or command keeps a `deprecated` class
attribute naming the version it goes and its replacement, and a rule refuses one past its
version. Until then, nothing on the wire is deprecated.

## A migration

A deploy restarts the gateway, the workers and the migration at different moments, and rolling
back is installing the wheel before: so a migration never breaks the release before it. A change
of shape goes in three steps, each its own release: **expand** (add the new table or column, one
the old release need not write: nullable, or with a default), **release** (the code that reads
and writes the new shape and stops reading the old), **contract** (drop what nothing reads).

Rule 22 reads every migration after the ones applied when it was written. One that drops a table
or a column, renames either, adds a `NOT NULL` column with no default, or sets `NOT NULL` on a
column, is refused unless it carries, for each thing it contracts, the line

```
-- pinecall:contracts <table>[.<column>] unread since <version>
```

naming the release that stopped reading it, which is out already (no later than the version
`pyproject.toml` says).
