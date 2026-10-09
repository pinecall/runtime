-- Where an org sends its calls' traces (tenancy/telemetry.py): an OTLP endpoint of its own, the
-- headers each export carries sealed in the vault (they are a credential), and whether a span
-- may carry what was said and what a tool got. One row per org, read by the worker at every
-- call's start beside the vendor keys. Expanding only: a new table.

CREATE TABLE org_telemetry (
    org text NOT NULL,
    endpoint text NOT NULL,
    ciphertext text,
    pii boolean NOT NULL DEFAULT false,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT org_telemetry_pkey PRIMARY KEY (org)
);
