-- 0020: a PERSON may run the box, not only a key out of its environment.
--
-- Until here the operator was `PINECALL_OPS_KEY`: a string in the box's environment, belonging to
-- no org, carrying no name. That is right for a script and wrong for a person — whoever owns the
-- box had to paste a systemd credential into a browser to open /admin, and the page could not say
-- who was looking. So a member may be marked as one, and their own key — the one they log in with
-- — opens /v1/ops/* as well as their org's doors.
--
-- It is a column and not a list in the environment because an email is unique per ORG and not per
-- box: `PINECALL_OPERATORS=ana@acme.com` would hand the box to whoever invited that address into
-- an org of their own. A row cannot be claimed that way. The default is false, so no migration
-- makes anybody one, and the only way to become one is to be made one by somebody who already
-- holds the ops key — which is what a box starts with and never loses.

ALTER TABLE members ADD COLUMN IF NOT EXISTS operator boolean NOT NULL DEFAULT false;

-- Every door of /v1/ops asks this question on every request, and the answer is one row.
CREATE INDEX IF NOT EXISTS members_operators ON members (id) WHERE operator;
