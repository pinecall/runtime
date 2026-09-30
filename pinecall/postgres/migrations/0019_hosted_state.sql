-- What the runner last said of a hosted app: the release it serves now and under which host
-- name, and the host name that failed to build or start, with why. A host name is one release
-- under one set of the org's secrets, so a failure is never tried again until either changes.
ALTER TABLE hosted_apps
    ADD COLUMN live_release integer,
    ADD COLUMN live_host text,
    ADD COLUMN failed_host text,
    ADD COLUMN failed_why text NOT NULL DEFAULT '',
    ADD COLUMN reported_by text NOT NULL DEFAULT '',
    ADD COLUMN reported_at timestamptz;
