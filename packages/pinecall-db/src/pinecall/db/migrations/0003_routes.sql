-- 0003: the routes an operator typed, which outrank whatever a running app declares.
--
-- A row is a door: (fleet, number) -> (agent, channel). The number is the key because a number IS
-- one door — whoever dials it reaches one agent, whatever channel carries it — so moving it is an
-- UPDATE of this one row and nothing else, which is the whole promise of `routes add`: a number
-- changes hands with no deploy. A route an app declared lives in the gateway's memory for as long
-- as its socket is open; this table is the half that survives a restart, and it wins. The order is
-- in docs/decisions/routes.md.

CREATE TABLE IF NOT EXISTS routes (
    fleet     text NOT NULL,
    number    text NOT NULL,
    agent     text NOT NULL,
    channel   text NOT NULL,
    -- When the operator typed it. The list is read in this order, so a fleet's routes come back
    -- the way they were added and a re-add keeps a number where the operator remembers seeing it.
    added_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fleet, number)
);
