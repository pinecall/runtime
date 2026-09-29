-- A persona is one agent's: one copy per agent it named, or, named for every agent, per agent the
-- org tuned in either world or whose own log it claimed. An org with no agent loses the row.

ALTER TABLE agent_personas ADD COLUMN agent text;
ALTER TABLE agent_personas DROP CONSTRAINT agent_personas_pkey;

WITH agents_of_the_org AS (
    SELECT DISTINCT org, agent FROM agent_config
    UNION
    SELECT DISTINCT org, agent FROM call_log_head
    WHERE call IS NULL AND org IS NOT NULL AND agent IS NOT NULL
),
fanned AS (
    SELECT persona.*, named.agent AS its_agent
    FROM agent_personas persona, unnest(persona.agents) AS named(agent)
    WHERE persona.agent IS NULL AND persona.agents <> '{}'
    UNION ALL
    SELECT persona.*, owned.agent AS its_agent
    FROM agent_personas persona JOIN agents_of_the_org owned ON owned.org = persona.org
    WHERE persona.agent IS NULL AND persona.agents = '{}'
)
INSERT INTO agent_personas (org, agent, name, about, goal, style, facts, state, author, set_at,
                            llm, tts, voice, accepts_when, declines_when, agents)
SELECT org, its_agent, name, about, goal, style, facts, state, author, set_at,
       llm, tts, voice, accepts_when, declines_when, '{}'
FROM fanned;

DELETE FROM agent_personas WHERE agent IS NULL;
ALTER TABLE agent_personas ALTER COLUMN agent SET NOT NULL;
ALTER TABLE agent_personas ADD CONSTRAINT agent_personas_pkey PRIMARY KEY (org, agent, name);
ALTER TABLE agent_personas DROP COLUMN agents;
