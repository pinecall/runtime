# The Evals screen

The screen was one chapter of ms-6, in the console. It was written as a tombstone on 2026-09-08,
the morning the console was deleted, and the console came back the same evening
([console.md](console.md)): the GATEWAY serves no page, and `pinecall ui` serves the console
itself on 127.0.0.1. `console/src/screens/evals/` and `screens/sessions/` are where they were.
What follows is what the screen is NOT — the rules that live in the runtime and outlive any UI
that reads them.

## What stayed in the runtime, and is still true

**A run's tally is a count, never an average.** `k/n held` counts the judgments the matrix
carries. A delta against a previous run NAMES the judgments that changed hands
(`broke: confirma-antes-de-agendar · haiku · consent`) rather than averaging goldens: only a
judgment BOTH runs put is compared, since one the older run never asked has nothing to have
changed from, and an average would make every new golden a regression on the day it lands.

**Two vocabularies, deliberately unmerged.** A run's cells are ring 3's check statuses (a boolean
`passed`, an operator's word); a call's judges answer ring 4's four (`held · broken · deferred ·
skipped`, a tenant's verdict). Renaming one into the other would make a grep for either useless.
See [scoring.md](scoring.md).

**An absent `passed` is a third thing.** `call.score.passed` is optional and its absence means
nobody answered — neither green nor red. `not_judged` is the log's word for it, and "no verdict
read yet" is a different fact from "no verdict in the log": nothing may conflate them.

**A verdict is one request, not a page of a call.** `call.score` is the entry the log SEALS on
(`TERMINAL_EVENT`), so on a finished call it is the entry at `last_seq`:

```
GET /v1/calls/{call}/events?after=<last_seq - 1>&types=call.score&limit=1
```

**`panel` is written by the runtime, and only there.** `call.score` carries `panel` beside
`judges`: every judge that was RUN, answered or not, written by `evals/score.py` on every path,
`[]` when nothing judged the call at all. `evals/score.py:_the_judges_of` is the one definition
of it; a second copy in TypeScript is the twin this repo's hygiene forbids. `panel` is optional on
the wire and its absence means the runtime that wrote the entry predates the field — never `[]`.

**`GET /v1/evals/runs?agent=<slug>` narrows by agent at the door** (one `where` on `runs.newest`),
which is what keeps two organisations out of one list and what makes a limit count this agent's
runs alone. The door still answers the whole fleet when nobody names an agent — that is what a
drift check over the box reads.
