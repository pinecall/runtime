-- When a call to the number last reached the box (the worker opened it): what tells a number that
-- rings here from one the carrier never sent. Written at most once a minute per number.
ALTER TABLE routes ADD COLUMN last_call_at timestamptz;
