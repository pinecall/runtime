-- The vendors a call ran on the box's own key: their usage is the operator's to bill. Kept with
-- the call's facts at its seal, and not on the wire.

ALTER TABLE call_facts ADD COLUMN lent text[] NOT NULL DEFAULT '{}';
