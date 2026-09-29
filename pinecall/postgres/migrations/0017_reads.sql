-- Who read what: a person reading a call's log or recording, the operator reading one off the box
-- or looking a number up for a traceback. The access log a breach notification is written from.
-- A row names the call or the number and never what it said; one row per reader, subject and kind
-- an hour, so a page that reads a call twenty times says it once.

CREATE TABLE reads (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    env text CHECK (env IN ('production', 'sandbox')),
    subject text NOT NULL,
    what text NOT NULL CHECK (what IN ('log', 'recording', 'traceback')),
    reader text NOT NULL,
    at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX reads_by_org ON reads (org, at DESC);
CREATE INDEX reads_by_subject ON reads (org, subject, at DESC);
