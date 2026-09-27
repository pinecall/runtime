-- The schema as it stood on 2026-09-27: pg_dump --schema-only of the sandbox database. The
-- extensions (vector, pg_textsearch) are created by the database, not here, and the runner owns
-- schema_migrations. Applied migrations are never edited: a change is a new file.

CREATE FUNCTION call_log_refuses_the_statement() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    raise exception 'call_log is append-only: % is refused', tg_op
        using errcode = 'restrict_violation';
end;
$$;

CREATE TABLE agent_config (
    org text NOT NULL,
    env text NOT NULL,
    holder text NOT NULL,
    agent text NOT NULL,
    version integer NOT NULL,
    config jsonb NOT NULL,
    author text NOT NULL,
    note text,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_config_env_check CHECK ((env = ANY (ARRAY['production'::text, 'sandbox'::text]))),
    CONSTRAINT agent_config_version_check CHECK ((version >= 1))
);

CREATE TABLE agent_personas (
    org text NOT NULL,
    agent text,
    name text NOT NULL,
    about text DEFAULT ''::text NOT NULL,
    goal text NOT NULL,
    style text NOT NULL,
    facts jsonb DEFAULT '{}'::jsonb NOT NULL,
    state jsonb DEFAULT '{}'::jsonb NOT NULL,
    author text DEFAULT ''::text NOT NULL,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    llm text,
    tts text,
    voice text,
    accepts_when text DEFAULT ''::text NOT NULL,
    declines_when text DEFAULT ''::text NOT NULL
);

CREATE TABLE agent_widgets (
    org text NOT NULL,
    env text NOT NULL,
    agent text NOT NULL,
    title text,
    tagline text,
    greeting text,
    accent text,
    autostart boolean DEFAULT false NOT NULL,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    theme text,
    CONSTRAINT agent_widgets_env_check CHECK ((env = ANY (ARRAY['production'::text, 'sandbox'::text])))
);

CREATE TABLE api_keys (
    id text NOT NULL,
    hash text NOT NULL,
    org text NOT NULL,
    label text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    env text NOT NULL,
    scopes text[] NOT NULL,
    subject text,
    name text,
    created_by text,
    last_used_at timestamp with time zone,
    expires_at timestamp with time zone,
    CONSTRAINT api_keys_env_check CHECK ((env = ANY (ARRAY['production'::text, 'sandbox'::text])))
);

CREATE TABLE box_settings (
    name text NOT NULL,
    value jsonb NOT NULL,
    ciphertext text,
    set_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE call_facts (
    call text NOT NULL,
    channel text,
    direction text,
    from_number text,
    to_number text,
    name text,
    contact text,
    spoken boolean DEFAULT false NOT NULL,
    ended_at double precision,
    end_reason text,
    outcome text,
    cost_eur double precision,
    judged integer,
    held integer,
    passed boolean,
    reason text,
    escalated boolean DEFAULT false NOT NULL,
    promised boolean DEFAULT false NOT NULL,
    e2e double precision[] DEFAULT '{}'::double precision[] NOT NULL,
    heard_at double precision[] DEFAULT '{}'::double precision[] NOT NULL,
    last_text text,
    last_at double precision,
    last_in boolean,
    persona text
);

CREATE TABLE call_log (
    call text,
    seq bigint NOT NULL,
    ts double precision NOT NULL,
    agent text NOT NULL,
    type text NOT NULL,
    ephemeral boolean NOT NULL,
    data jsonb NOT NULL,
    log text GENERATED ALWAYS AS (COALESCE(call, ('@'::text || agent))) STORED NOT NULL,
    "position" bigint NOT NULL,
    CONSTRAINT call_log_call_never_opens_with_at CHECK (((call IS NULL) OR ("left"(call, 1) <> '@'::text)))
);

CREATE TABLE call_log_head (
    log text NOT NULL,
    agent text,
    call text,
    seq bigint DEFAULT 0 NOT NULL,
    sealed boolean DEFAULT false NOT NULL,
    started_at double precision,
    org text,
    env text,
    holder text,
    config_version integer,
    lexicon_version integer
);

ALTER TABLE call_log ALTER COLUMN "position" ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME call_log_position_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE carriers (
    org text NOT NULL,
    kind text NOT NULL,
    account text NOT NULL,
    ciphertext text NOT NULL,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT carriers_kind_check CHECK ((kind = ANY (ARRAY['twilio'::text, 'sip'::text])))
);

CREATE TABLE contact_memories (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org text NOT NULL,
    contact text NOT NULL,
    text text NOT NULL,
    category text,
    embedding halfvec(1024) NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    invalidated_at timestamp with time zone,
    supersedes uuid,
    source_call text,
    confidence real DEFAULT 1.0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    env text NOT NULL,
    holder text NOT NULL,
    CONSTRAINT contact_memories_env_check CHECK ((env = ANY (ARRAY['production'::text, 'sandbox'::text])))
);

CREATE TABLE dial_policy (
    org text NOT NULL,
    dial_anywhere boolean,
    per_minute integer,
    per_day integer,
    countries text[],
    max_duration_s integer,
    set_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE dials (
    id bigint NOT NULL,
    org text NOT NULL,
    env text NOT NULL,
    agent text NOT NULL,
    call text,
    dialled text NOT NULL,
    shown text,
    asked_by text NOT NULL,
    refused text,
    at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dials_env_check CHECK ((env = ANY (ARRAY['production'::text, 'sandbox'::text])))
);

CREATE SEQUENCE dials_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE dials_id_seq OWNED BY dials.id;

CREATE TABLE eval_runs (
    id text NOT NULL,
    agent text NOT NULL,
    started_at double precision NOT NULL,
    finished_at double precision,
    status text NOT NULL,
    document jsonb NOT NULL
);

CREATE TABLE hold_audio (
    org text NOT NULL,
    agent text NOT NULL,
    played text NOT NULL,
    audio bytea,
    sha256 text,
    seconds real,
    name text,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT hold_audio_check CHECK (((played = 'custom'::text) = ((audio IS NOT NULL) AND (sha256 IS NOT NULL)))),
    CONSTRAINT hold_audio_played_check CHECK ((played = ANY (ARRAY['off'::text, 'custom'::text])))
);

CREATE TABLE invitations (
    token_hash text NOT NULL,
    member text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    spent_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    vouched boolean DEFAULT false NOT NULL
);

CREATE TABLE knowledge_bases (
    org text NOT NULL,
    base text NOT NULL,
    model text NOT NULL,
    dimensions integer NOT NULL,
    chunks integer NOT NULL,
    pushed_at timestamp with time zone DEFAULT now() NOT NULL,
    env text NOT NULL,
    holder text NOT NULL,
    CONSTRAINT knowledge_bases_env_check CHECK ((env = ANY (ARRAY['production'::text, 'sandbox'::text])))
);

CREATE TABLE knowledge_chunks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org text NOT NULL,
    base text NOT NULL,
    path text NOT NULL,
    heading text,
    ordinal integer NOT NULL,
    text text NOT NULL,
    embedding halfvec(1024),
    env text NOT NULL,
    holder text NOT NULL,
    mode text DEFAULT 'retrieved'::text NOT NULL,
    CONSTRAINT knowledge_chunks_env_check CHECK ((env = ANY (ARRAY['production'::text, 'sandbox'::text]))),
    CONSTRAINT knowledge_chunks_mode_check CHECK ((mode = ANY (ARRAY['retrieved'::text, 'whole'::text])))
);

CREATE TABLE knowledge_files (
    org text NOT NULL,
    env text NOT NULL,
    holder text NOT NULL,
    base text NOT NULL,
    path text NOT NULL,
    text text NOT NULL,
    chunks integer NOT NULL,
    pushed_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE lexicon (
    org text NOT NULL,
    env text NOT NULL,
    holder text NOT NULL,
    version integer NOT NULL,
    said jsonb DEFAULT '{}'::jsonb NOT NULL,
    heard jsonb DEFAULT '[]'::jsonb NOT NULL,
    author text NOT NULL,
    note text,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT lexicon_env_check CHECK ((env = ANY (ARRAY['production'::text, 'sandbox'::text]))),
    CONSTRAINT lexicon_version_check CHECK ((version >= 1))
);

CREATE TABLE members (
    id text NOT NULL,
    org text NOT NULL,
    email text NOT NULL,
    name text NOT NULL,
    role text NOT NULL,
    agents text[] DEFAULT '{}'::text[] NOT NULL,
    status text NOT NULL,
    password_hash text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    operator boolean DEFAULT false NOT NULL,
    production boolean DEFAULT false NOT NULL,
    verified_at timestamp with time zone,
    CONSTRAINT members_role_check CHECK ((role = ANY (ARRAY['qa'::text, 'supervisor'::text, 'manager'::text, 'admin'::text, 'developer'::text]))),
    CONSTRAINT members_status_check CHECK ((status = ANY (ARRAY['invited'::text, 'active'::text, 'disabled'::text])))
);

CREATE TABLE org_mail (
    org text NOT NULL,
    host text NOT NULL,
    port integer NOT NULL,
    security text NOT NULL,
    username text NOT NULL,
    ciphertext text NOT NULL,
    sender text NOT NULL,
    verified_at timestamp with time zone,
    last_error text,
    set_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE org_sso (
    org text NOT NULL,
    issuer text NOT NULL,
    client_id text NOT NULL,
    ciphertext text NOT NULL,
    domains text[] NOT NULL,
    role text,
    required boolean DEFAULT false NOT NULL,
    set_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE orgs (
    id text NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    judging boolean
);

CREATE TABLE outbound_trunks (
    org text NOT NULL,
    kind text NOT NULL,
    trunk_id text NOT NULL,
    address text NOT NULL,
    username text,
    ciphertext text,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT outbound_trunks_kind_check CHECK ((kind = ANY (ARRAY['twilio'::text, 'sip'::text])))
);

CREATE TABLE provider_keys (
    org text NOT NULL,
    vendor text NOT NULL,
    ciphertext text NOT NULL,
    set_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE quotas (
    org text NOT NULL,
    minutes integer,
    messages integer,
    agents integer,
    concurrent_calls integer,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    memory_facts integer,
    knowledge_chunks integer,
    numbers integer,
    seats integer,
    budget_eur integer,
    lends text[],
    llm_tokens bigint
);

CREATE TABLE routes (
    org text NOT NULL,
    number text NOT NULL,
    agent text NOT NULL,
    channel text NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    env text NOT NULL,
    managed boolean DEFAULT false NOT NULL,
    CONSTRAINT routes_env_check CHECK ((env = ANY (ARRAY['production'::text, 'sandbox'::text])))
);

CREATE TABLE thread_reads (
    org text NOT NULL,
    env text NOT NULL,
    holder text NOT NULL,
    agent text NOT NULL,
    reader text NOT NULL,
    contact text NOT NULL,
    read_at double precision NOT NULL
);

CREATE TABLE tokens (
    call text NOT NULL,
    org text NOT NULL,
    agent text NOT NULL,
    scope text NOT NULL,
    minted_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    spent_at timestamp with time zone
);

ALTER TABLE ONLY dials ALTER COLUMN id SET DEFAULT nextval('dials_id_seq'::regclass);

ALTER TABLE ONLY agent_config
    ADD CONSTRAINT agent_config_pkey PRIMARY KEY (org, env, holder, agent, version);

ALTER TABLE ONLY agent_personas
    ADD CONSTRAINT agent_personas_pkey PRIMARY KEY (org, name);

ALTER TABLE ONLY agent_widgets
    ADD CONSTRAINT agent_widgets_pkey PRIMARY KEY (org, env, agent);

ALTER TABLE ONLY api_keys
    ADD CONSTRAINT api_keys_hash_key UNIQUE (hash);

ALTER TABLE ONLY api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);

ALTER TABLE ONLY box_settings
    ADD CONSTRAINT box_settings_pkey PRIMARY KEY (name);

ALTER TABLE ONLY call_facts
    ADD CONSTRAINT call_facts_pkey PRIMARY KEY (call);

ALTER TABLE ONLY call_log_head
    ADD CONSTRAINT call_log_head_pkey PRIMARY KEY (log);

ALTER TABLE ONLY call_log
    ADD CONSTRAINT call_log_one_row_per_seq PRIMARY KEY (log, seq);

ALTER TABLE ONLY carriers
    ADD CONSTRAINT carriers_pkey PRIMARY KEY (org);

ALTER TABLE ONLY contact_memories
    ADD CONSTRAINT contact_memories_pkey PRIMARY KEY (id);

ALTER TABLE ONLY dial_policy
    ADD CONSTRAINT dial_policy_pkey PRIMARY KEY (org);

ALTER TABLE ONLY dials
    ADD CONSTRAINT dials_pkey PRIMARY KEY (id);

ALTER TABLE ONLY eval_runs
    ADD CONSTRAINT eval_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY hold_audio
    ADD CONSTRAINT hold_audio_pkey PRIMARY KEY (org, agent);

ALTER TABLE ONLY invitations
    ADD CONSTRAINT invitations_pkey PRIMARY KEY (token_hash);

ALTER TABLE ONLY knowledge_bases
    ADD CONSTRAINT knowledge_bases_pkey PRIMARY KEY (org, env, holder, base);

ALTER TABLE ONLY knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY knowledge_files
    ADD CONSTRAINT knowledge_files_pkey PRIMARY KEY (org, env, holder, base, path);

ALTER TABLE ONLY lexicon
    ADD CONSTRAINT lexicon_pkey PRIMARY KEY (org, env, holder, version);

ALTER TABLE ONLY members
    ADD CONSTRAINT members_org_email_key UNIQUE (org, email);

ALTER TABLE ONLY members
    ADD CONSTRAINT members_pkey PRIMARY KEY (id);

ALTER TABLE ONLY org_mail
    ADD CONSTRAINT org_mail_pkey PRIMARY KEY (org);

ALTER TABLE ONLY org_sso
    ADD CONSTRAINT org_sso_pkey PRIMARY KEY (org);

ALTER TABLE ONLY orgs
    ADD CONSTRAINT orgs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY orgs
    ADD CONSTRAINT orgs_slug_key UNIQUE (slug);

ALTER TABLE ONLY outbound_trunks
    ADD CONSTRAINT outbound_trunks_pkey PRIMARY KEY (org);

ALTER TABLE ONLY provider_keys
    ADD CONSTRAINT provider_keys_pkey PRIMARY KEY (org, vendor);

ALTER TABLE ONLY quotas
    ADD CONSTRAINT quotas_pkey PRIMARY KEY (org);

ALTER TABLE ONLY routes
    ADD CONSTRAINT routes_pkey PRIMARY KEY (org, number);

ALTER TABLE ONLY thread_reads
    ADD CONSTRAINT thread_reads_pkey PRIMARY KEY (org, env, holder, agent, reader, contact);

ALTER TABLE ONLY tokens
    ADD CONSTRAINT tokens_pkey PRIMARY KEY (call);

CREATE INDEX call_facts_by_contact ON call_facts USING btree (contact) WHERE (contact IS NOT NULL);

CREATE INDEX call_facts_by_persona ON call_facts USING btree (persona) WHERE (persona IS NOT NULL);

CREATE INDEX call_log_head_by_agent ON call_log_head USING btree (agent, started_at) WHERE (call IS NOT NULL);

CREATE INDEX call_log_head_by_corner ON call_log_head USING btree (org, env, holder, started_at DESC) WHERE (call IS NOT NULL);

CREATE INDEX call_log_metered ON call_log USING btree (type, "position");

CREATE INDEX contact_memories_current ON contact_memories USING btree (org, env, holder, contact) WHERE (invalidated_at IS NULL);

CREATE INDEX contact_memories_embedding_hnsw ON contact_memories USING hnsw (embedding halfvec_cosine_ops);

CREATE INDEX contact_memories_text_bm25 ON contact_memories USING bm25 (text) WITH (text_config=spanish);

CREATE INDEX dials_by_org ON dials USING btree (org, at DESC);

CREATE INDEX eval_runs_newest_first ON eval_runs USING btree (started_at DESC);

CREATE INDEX knowledge_chunks_by_base ON knowledge_chunks USING btree (org, env, holder, base);

CREATE INDEX knowledge_chunks_embedding_hnsw ON knowledge_chunks USING hnsw (embedding halfvec_cosine_ops);

CREATE INDEX knowledge_chunks_text_bm25 ON knowledge_chunks USING bm25 (text) WITH (text_config=spanish);

CREATE INDEX members_operators ON members USING btree (id) WHERE operator;

CREATE TRIGGER call_log_refuses_delete BEFORE DELETE ON call_log FOR EACH STATEMENT EXECUTE FUNCTION call_log_refuses_the_statement();

CREATE TRIGGER call_log_refuses_update BEFORE UPDATE ON call_log FOR EACH STATEMENT EXECUTE FUNCTION call_log_refuses_the_statement();

ALTER TABLE ONLY agent_config
    ADD CONSTRAINT agent_config_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY agent_personas
    ADD CONSTRAINT agent_personas_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY agent_widgets
    ADD CONSTRAINT agent_widgets_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY carriers
    ADD CONSTRAINT carriers_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY contact_memories
    ADD CONSTRAINT contact_memories_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY contact_memories
    ADD CONSTRAINT contact_memories_supersedes_fkey FOREIGN KEY (supersedes) REFERENCES contact_memories(id);

ALTER TABLE ONLY dial_policy
    ADD CONSTRAINT dial_policy_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY invitations
    ADD CONSTRAINT invitations_member_fkey FOREIGN KEY (member) REFERENCES members(id) ON DELETE CASCADE;

ALTER TABLE ONLY knowledge_bases
    ADD CONSTRAINT knowledge_bases_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_org_env_holder_base_fkey FOREIGN KEY (org, env, holder, base) REFERENCES knowledge_bases(org, env, holder, base) ON DELETE CASCADE;

ALTER TABLE ONLY knowledge_files
    ADD CONSTRAINT knowledge_files_org_env_holder_base_fkey FOREIGN KEY (org, env, holder, base) REFERENCES knowledge_bases(org, env, holder, base) ON DELETE CASCADE;

ALTER TABLE ONLY lexicon
    ADD CONSTRAINT lexicon_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY members
    ADD CONSTRAINT members_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY org_mail
    ADD CONSTRAINT org_mail_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY org_sso
    ADD CONSTRAINT org_sso_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY outbound_trunks
    ADD CONSTRAINT outbound_trunks_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY provider_keys
    ADD CONSTRAINT provider_keys_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

ALTER TABLE ONLY quotas
    ADD CONSTRAINT quotas_org_fkey FOREIGN KEY (org) REFERENCES orgs(id) ON DELETE CASCADE;

-- The org the first key is issued against, and the one a verb that names none falls back to.
INSERT INTO orgs (id, slug, name) VALUES ('default', 'default', 'default') ON CONFLICT DO NOTHING;
