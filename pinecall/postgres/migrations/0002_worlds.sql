-- One database serves both worlds, so an org's limits and an agent's hold melody are kept per
-- world, as the tuning, the lexicon and the widget already are. A row written before this
-- migration is production's: the sandbox's rows arrive with their world at the cutover.

ALTER TABLE quotas ADD COLUMN env text NOT NULL DEFAULT 'production'
    CONSTRAINT quotas_env_check CHECK (env = ANY (ARRAY['production'::text, 'sandbox'::text]));
ALTER TABLE quotas ALTER COLUMN env DROP DEFAULT;
ALTER TABLE quotas DROP CONSTRAINT quotas_pkey;
ALTER TABLE quotas ADD CONSTRAINT quotas_pkey PRIMARY KEY (org, env);

ALTER TABLE hold_audio ADD COLUMN env text NOT NULL DEFAULT 'production'
    CONSTRAINT hold_audio_env_check CHECK (env = ANY (ARRAY['production'::text, 'sandbox'::text]));
ALTER TABLE hold_audio ALTER COLUMN env DROP DEFAULT;
ALTER TABLE hold_audio DROP CONSTRAINT hold_audio_pkey;
ALTER TABLE hold_audio ADD CONSTRAINT hold_audio_pkey PRIMARY KEY (org, env, agent);
