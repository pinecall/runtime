-- A lexicon is one agent's. Every row the org wrote is copied to each agent the org has in that
-- world, keeping its version so a call's lexicon_version still reads it; then the org's rows go.
-- The agents an org has: those with settings in the world, and those whose own log it claimed
-- (an agent's log carries no world, so it counts in both).

ALTER TABLE lexicon ADD COLUMN agent text;
ALTER TABLE lexicon DROP CONSTRAINT lexicon_pkey;

WITH org_agents AS (
    SELECT DISTINCT org, env, agent FROM agent_config
    UNION
    SELECT head.org, world.env, head.agent
    FROM call_log_head head
    CROSS JOIN (VALUES ('production'), ('sandbox')) AS world (env)
    WHERE head.call IS NULL AND head.org IS NOT NULL AND head.agent IS NOT NULL
)
INSERT INTO lexicon (org, env, holder, agent, version, said, heard, author, note, set_at)
SELECT lexicon.org, lexicon.env, lexicon.holder, org_agents.agent, lexicon.version, lexicon.said,
       lexicon.heard, lexicon.author, lexicon.note, lexicon.set_at
FROM lexicon
JOIN org_agents ON org_agents.org = lexicon.org AND org_agents.env = lexicon.env
WHERE lexicon.agent IS NULL;

DELETE FROM lexicon WHERE agent IS NULL;

ALTER TABLE lexicon ALTER COLUMN agent SET NOT NULL;
ALTER TABLE lexicon ADD CONSTRAINT lexicon_pkey PRIMARY KEY (org, env, holder, agent, version);
