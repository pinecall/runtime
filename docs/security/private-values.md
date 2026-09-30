# Private values — what a call's log keeps of what an agent declared private

An agent may mark values private in two places: a tool's arguments (`pii` on the tool, a list of
argument names) and the fields of its state (a field declared with visibility `pii`). This page is
what the runtime does with them, so a tenant can say where a phone number the caller gave ends up,
and check it.

## Masked when written, not when read

A call's log is read by many things besides the console: the export, the facts the console lists,
the judges (whose model is a vendor's), the operator's `sessions show` on the box, a query on the
database. A mask applied by each reader protects only the readers that remember it. So the mask is
applied once, where the entry is written, by the gateway, and every reader reads the masked entry:

| entry | what the log keeps |
|---|---|
| `tool.call` | `arguments`, with every name the tool lists under `pii` as `***` |
| `state.changed` · `call.attached` | `state`, with every field declared `pii` as `***` |

The key stays, so a reader knows a value exists; the value goes whole, so nothing of it leaks
through its type. The declaration read is the one the call was opened on, with what a
`session.configure` sent for that call alone.

## What still has the value, and why

Masking must not change the call, and three things in it need the value itself:

| who | why | how it gets it |
|---|---|---|
| the app's socket | it runs the tool with the arguments, and its state is its own | the gateway sends it the entry as it was written, from memory |
| the socket that takes a call over | `call.attached` hands it the state to carry on from | read back from the sealed copy |
| a written call taken up after a restart | the model reads its earlier tool calls as they were made | read back from the sealed copy |

The sealed copy is a row of `call_private` per entry that held a private value: the values
alone, one Fernet token under `PINECALL_VAULT_KEY`, keyed by the entry's log and seq. Nothing reads
it but those two paths; the doors that read a log, the export, the judges and the facts never do.
`pinecall-runtime vault rotate` re-seals it with every other secret, and an erasure (of the call, of
a contact, of the org, or the nightly retention run) deletes it with the log. A value sealed under
a key the vault no longer lists reads as `***`.

The worker that runs a spoken call holds the values in memory while the call runs, as it holds
everything the call says; it keeps nothing after it.

## What is not masked

- **What was said.** The caller who reads out their phone number is heard, transcribed and kept
  in `turn.user`; an agent's reply that repeats it is kept in `turn.agent`. Transcripts are never
  touched.
- **A tool's result.** `pii` names arguments; what the tool answered is kept as it came, and is
  what the model read.
- **Metrics, and the call's own numbers** (`from`, `to`), which a call is found by.

## Logs written before the mask

Entries written before this was built keep their values in the log as they were written: the log
is append-only, and nothing rewrites it. The tenant projection masks them when read, by the
declaration the reader reads with ([../protocol/projections.md](../protocol/projections.md)); the
export and a query on the database still see them until the call is erased or its retention runs.
