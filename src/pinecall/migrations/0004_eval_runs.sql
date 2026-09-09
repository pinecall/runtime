-- 0004: the eval runs, one row each, so a suite that ran last week can still be diffed.
--
-- A run is not a log: its calls each write one of those, and this row only says which calls it
-- opened and what the graphs answered about them. The document is the whole run as the door
-- answers it, kept as one jsonb rather than spread across columns, because the matrix's shape is
-- DeepEval's and a table that mirrored it would have to be migrated every time a graph is added.
-- The four columns beside it are the ones a query actually filters on. See docs/decisions/eval-runner.md.

create table if not exists eval_runs (
    id          text primary key,
    agent       text not null,
    -- Seconds since the epoch, by the clock of the process that ran it: the same double the log's
    -- own entries carry, so a run and the calls it opened are read on one timeline.
    started_at  double precision not null,
    finished_at double precision,
    -- running · done · failed. `failed` is the run breaking, never a golden that did not hold.
    status      text not null,
    document    jsonb not null
);

-- The one order a person asks in: what did the last run say, and the one before it.
create index if not exists eval_runs_newest_first on eval_runs (started_at desc);
