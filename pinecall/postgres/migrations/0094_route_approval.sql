-- A number the org hooked itself (pointed at the box from its own carrier) proves nothing about
-- whose it is, so the operator approves it before a call to it is opened (channels/routes.py);
-- a number bought, imported from an account of the org or typed by the operator is approved as
-- it is written. Every row already written keeps answering: it reads approved at this migration.
-- New columns with a default: a release before this one writes rows that read approved, as it
-- answered them.
ALTER TABLE routes ADD COLUMN approved_at timestamptz DEFAULT now();
ALTER TABLE routes ADD COLUMN approved_by text;

-- One org holds a number: a second org's row for it was "the older one answers". Refused here if
-- the box holds a number twice; the operator lets one of them go first (GET /v1/ops/numbers).
CREATE UNIQUE INDEX routes_number_held_once ON routes (number);
