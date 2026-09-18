-- 0036: the melody a caller hears while a tool runs, when an agent's is not the one it ships with.
--
-- Every agent plays the runtime's own melody (session/a-new-life.ogg) while a tool runs, and this
-- table holds only the agents that were told otherwise from the pipeline door: `off`, or a clip a
-- person uploaded, stored as the runtime converted it — Ogg Opus, 48 kHz mono, a few hundred
-- kilobytes at most — so every worker, on whichever box, fetches the same bytes by the same hash.
-- No row is the default; nothing is backfilled.

CREATE TABLE IF NOT EXISTS hold_audio (
    org      text        NOT NULL,
    agent    text        NOT NULL,
    played   text        NOT NULL CHECK (played IN ('off', 'custom')),
    audio    bytea,
    sha256   text,
    seconds  real,
    name     text,
    set_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, agent),
    CHECK ((played = 'custom') = (audio IS NOT NULL AND sha256 IS NOT NULL))
);
