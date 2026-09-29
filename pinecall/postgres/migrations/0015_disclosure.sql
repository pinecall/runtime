-- What a call says before its greeting. `disclosure` is the org's own opening sentence for an
-- outbound call: NULL is the platform's ("an automated assistant calling on behalf of <org>"), ''
-- is none. `recording_notice` says "this call may be recorded" on every recorded spoken call.

ALTER TABLE org_policy
    ADD COLUMN disclosure text,
    ADD COLUMN recording_notice boolean NOT NULL DEFAULT true;
