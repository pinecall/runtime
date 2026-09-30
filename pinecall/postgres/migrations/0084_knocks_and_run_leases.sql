-- What must be counted or held across the gateways of a box, in the one place they share.
--
-- knocks: each time a name knocked at a sign-in door or asked for a voice sample
-- (tenancy/knocks.py). Five knocks a minute must mean five, whichever gateway each lands on;
-- these doors are rare, so a row and a lock each cost nothing that matters. Rows past the window
-- are deleted as knocks come in.
--
-- run_leases: the eval run each agent is under (evals/runs.py). A run takes its agent's lease and
-- renews it while it runs; a gateway that dies mid-run leaves a lease that runs out on its own.
-- New tables: CREATE TABLE locks nothing that exists.
CREATE TABLE knocks (
    name text NOT NULL,
    at double precision NOT NULL
);

CREATE INDEX knocks_by_name ON knocks (name, at);
CREATE INDEX knocks_by_age ON knocks (at);

CREATE TABLE run_leases (
    agent text PRIMARY KEY,
    run text NOT NULL,
    until timestamptz NOT NULL
);
