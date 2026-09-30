-- What the box hosts for an org: an app it runs from sources the org uploaded. One row per app
-- and world, with the server's token minted for its process, sealed; every upload is a release,
-- numbered from 1 and never edited. The secrets are what its process is started with.
CREATE TABLE hosted_apps (
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    env text NOT NULL CHECK (env IN ('production', 'sandbox')),
    name text NOT NULL,
    key_fingerprint text NOT NULL,
    sealed_key text NOT NULL,
    created_by text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, env, name)
);

CREATE TABLE hosted_releases (
    org text NOT NULL,
    env text NOT NULL,
    name text NOT NULL,
    release integer NOT NULL CHECK (release >= 1),
    source bytea NOT NULL,
    sha256 text NOT NULL,
    bytes integer NOT NULL,
    author text NOT NULL DEFAULT '',
    note text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, env, name, release),
    FOREIGN KEY (org, env, name) REFERENCES hosted_apps (org, env, name) ON DELETE CASCADE
);

CREATE TABLE org_secrets (
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    env text NOT NULL CHECK (env IN ('production', 'sandbox')),
    name text NOT NULL,
    sealed text NOT NULL,
    set_by text NOT NULL DEFAULT '',
    set_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, env, name)
);

-- How many apps the box hosts for the org in a world; NULL is no limit, 0 hosts none.
ALTER TABLE quotas ADD COLUMN hosted_apps integer;
