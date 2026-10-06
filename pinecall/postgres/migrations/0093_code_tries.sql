-- How many codes each call keyed (tenancy/codes.py): a call tries three, then no page is tied to
-- it, whatever it keys. Kept here so the count holds whichever gateway serves the call; a row
-- leaves a day after its call's first try.
-- A new table: CREATE TABLE locks nothing that exists.
CREATE TABLE code_tries (
    call text PRIMARY KEY,
    tries integer NOT NULL,
    first_at double precision NOT NULL
);
CREATE INDEX code_tries_first_at ON code_tries (first_at);
