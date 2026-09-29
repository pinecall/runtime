-- The org's outbound calling rules, beside its retention: the hours of the called number's day a
-- call may ring, and how many times one number may be rung in a day. Unset is the platform's
-- default by destination (tenancy/dial_policy.py): the US floor for a +1 number, nothing elsewhere.

ALTER TABLE org_policy
    ADD COLUMN calling_from smallint CHECK (calling_from BETWEEN 0 AND 23),
    ADD COLUMN calling_until smallint CHECK (calling_until BETWEEN 1 AND 24),
    ADD COLUMN per_number_day integer CHECK (per_number_day > 0),
    ADD CONSTRAINT org_policy_calling_hours_whole CHECK (
        (calling_from IS NULL AND calling_until IS NULL)
        OR (calling_from IS NOT NULL AND calling_until IS NOT NULL AND calling_from < calling_until)
    );
