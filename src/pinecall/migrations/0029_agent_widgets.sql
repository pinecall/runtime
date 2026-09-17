-- 0029: how the widget presents an agent, kept by the gateway.
--
-- The widget reads its name, a line under it, a colour and now a greeting and whether to start on
-- open from its own attributes, which a page's author writes by hand. A console that lets a person
-- set them and copy the snippet needs a place to keep what was set: one row per org, world and
-- agent, because a sandbox copy of an agent is tried with other words than the one the site shows.
-- A NULL is the widget's own default.

CREATE TABLE IF NOT EXISTS agent_widgets (
    org        text NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    env        text NOT NULL CHECK (env IN ('production', 'sandbox')),
    agent      text NOT NULL,
    title      text,
    tagline    text,
    greeting   text,
    accent     text,
    autostart  boolean NOT NULL DEFAULT false,
    set_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, env, agent)
);
