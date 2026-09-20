-- 0042: the personas, kept by the gateway. A synthetic caller was a file of the project —
-- test/<agent>/personas/<name>.ts, default-exporting an object — which is why only the terminal
-- standing in that directory could list one, and why the production console could show none at
-- all. A persona is not code: it is a goal, a manner and a handful of facts, the same kind of
-- thing as the voice, the lexicon and what the agent knows by heart, and those are the world's
-- (0038). So it moves here with them: written from the console or the CLI, read by `pinecall
-- simulate` and by Simulations, and no deploy between writing one and calling with it.
--
-- One list per agent per ORG, and not per world: a caller is a test, not something a customer
-- hears, and a team that wrote `price-shopper` once should not write it again for production.
-- The name is the key, as the file's name was.

CREATE TABLE IF NOT EXISTS agent_personas (
    org      text        NOT NULL,
    agent    text        NOT NULL,
    name     text        NOT NULL,
    about    text        NOT NULL DEFAULT '',
    goal     text        NOT NULL,
    style    text        NOT NULL,
    facts    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    state    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    author   text        NOT NULL DEFAULT '',
    set_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, agent, name)
);
