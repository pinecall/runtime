-- 0012: what an operator turned at the pipeline door survives a restart. One row per (org, agent).
--
-- Until here the knobs lived in a dict on app.state: an operator moved an agent onto another model
-- from the console, the gateway was deployed that evening, and the agent went back to what its
-- class declared with nobody told. convo kept these rows in its own store from the start
-- (convo/state/overrides.py) and this is the same idea over the org table.
--
-- The whole set is one row, replaced whole, because that is what the door already does: a knob
-- left out of the PUT body is a knob given back to the app, and NULL here means exactly that.
-- There is no blank value that means anything — an empty voice once silenced a line of calls, and
-- providers/overrides.py refuses one at the door.
--
-- The cache in front of this table is one process's memory of it, filled once at startup and
-- written through on every turn. See docs/decisions/pipeline.md.

CREATE TABLE IF NOT EXISTS pipeline_overrides (
    org         text NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    agent       text NOT NULL,
    stt         text,
    llm         text,
    voice       text,
    tts_model   text,
    greeting    text,
    set_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, agent)
);
