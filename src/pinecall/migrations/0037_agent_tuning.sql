-- 0037: an agent's tuning is the org's, per world, per corner, and every version of it is kept.
--
-- Until here what an agent runs on lived in two places: the fields of its class, sent by the app
-- on every reconnect and held in the gateway's memory, and `pipeline_overrides` (0012), six knobs
-- an operator turned from the console, laid over the class with nobody told. Two sources of truth:
-- a developer changed the model in the class, deployed, and nothing happened because the table
-- still said otherwise. And the table had no world — a knob turned on a sandbox key changed
-- production's next call — and no corner, so six people sharing one sandbox would have changed
-- each other's voice mid-test: the failure 0021 fixed for knowledge and memory.
--
-- From here the tuning is one table, keyed the way knowledge is (org, world, corner) and versioned:
-- a row is never updated, a change is the next version, and a call's head row says which version
-- it ran on. The org's own corner is '' and not NULL, as 0021 says: it is part of the key. What a
-- knob may be is providers/tuning.py's; the columns hold JSON and no meaning.
--
-- The lexicon is the org's words — how the voice says a brand, what the ears must know — shared by
-- every agent of it, so it has no agent column and is otherwise the same shape.
--
-- Every turned set becomes version 1 of BOTH worlds: the old row had no world and was read by
-- both, so the copy is what the rows already meant, not a courtesy. `pipeline_overrides` itself
-- stays one release, read by nothing: a table is dropped in two migrations, the code first.

CREATE TABLE IF NOT EXISTS agent_config (
    org      text        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    env      text        NOT NULL CHECK (env IN ('production', 'sandbox')),
    holder   text        NOT NULL,
    agent    text        NOT NULL,
    version  integer     NOT NULL CHECK (version >= 1),
    config   jsonb       NOT NULL,
    author   text        NOT NULL,
    note     text,
    set_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, env, holder, agent, version)
);

CREATE TABLE IF NOT EXISTS lexicon (
    org      text        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    env      text        NOT NULL CHECK (env IN ('production', 'sandbox')),
    holder   text        NOT NULL,
    version  integer     NOT NULL CHECK (version >= 1),
    said     jsonb       NOT NULL DEFAULT '{}',
    heard    jsonb       NOT NULL DEFAULT '[]',
    author   text        NOT NULL,
    note     text,
    set_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, env, holder, version)
);

INSERT INTO agent_config (org, env, holder, agent, version, config, author, note)
SELECT o.org, w.env, '', o.agent, 1,
       jsonb_strip_nulls(jsonb_build_object(
           'voice', o.voice, 'tts', o.tts, 'tts_model', o.tts_model, 'stt', o.stt, 'llm', o.llm,
           'greeting', CASE WHEN o.greeting IS NULL THEN NULL
                            ELSE jsonb_build_object('say', o.greeting) END)),
       'migration', 'pipeline_overrides, 0012'
  FROM pipeline_overrides o
 CROSS JOIN (VALUES ('production'), ('sandbox')) AS w (env)
ON CONFLICT DO NOTHING;

-- The `words` scope — the lexicon, the opening's words, what is remembered — is what the floor
-- fixes without a developer, so every key that holds the floor (`supervise`: a supervisor's, a
-- manager's, an admin's, a developer's) holds it from here, as the presets now mint it. Nothing
-- a live key could do yesterday is refused today, which is the one thing a migration over a
-- running box may promise. A qa key reads, and holds none of it.
UPDATE api_keys
   SET scopes = array_append(scopes, 'words')
 WHERE 'supervise' = ANY (scopes) AND NOT ('words' = ANY (scopes));

-- Which tuning and which lexicon a call was built on, beside whose corner it was (0024): the two
-- numbers that make "what did this call run on" a read and not a guess. NULL is a corner that had
-- set nothing when the call opened, and every row already here.
ALTER TABLE call_log_head ADD COLUMN IF NOT EXISTS config_version integer;
ALTER TABLE call_log_head ADD COLUMN IF NOT EXISTS lexicon_version integer;
