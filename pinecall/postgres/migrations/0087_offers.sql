-- A room with a caller and no worker yet, and the worker the gateway offered it to
-- (gateway/dispatching/). LiveKit offers a job once and remembers nothing: a worker that declines
-- it, or a dead one LiveKit still lists, leaves the caller in silence. The gateway keeps the room
-- here from the moment a caller joins until a worker opens its call (the row is then deleted),
-- offers it to a worker it chose, and offers it again to another when nobody opened it in time.
-- Any gateway may sweep it: an offer is taken by the UPDATE that finds the count it read, so two
-- gateways never offer one room twice.
-- New table: CREATE TABLE locks nothing that exists.
CREATE TABLE offers (
    room text PRIMARY KEY,
    fleet text NOT NULL,
    -- The call's dispatch, as the SIP rule or the visitor's token carried it into the room.
    dispatch text NOT NULL,
    seen_at double precision NOT NULL,
    worker text,
    offers integer NOT NULL DEFAULT 0,
    offered_at double precision
);

CREATE INDEX offers_due ON offers (offered_at);
CREATE INDEX offers_by_age ON offers (seen_at);
