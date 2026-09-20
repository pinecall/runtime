-- 0043: the personas belong to an org, and the table finally says so.
--
-- 0042 wrote `org text NOT NULL` and stopped there, so `agent_personas` was the one per-org table
-- with no line back to `orgs`. Every other one — the quotas, the keys, the memories, the bases,
-- the widgets, the tuning — is `REFERENCES orgs (id) ON DELETE CASCADE`, which is what makes an
-- org's deletion take its rows with it. Without it, deleting an org left its synthetic callers
-- behind, addressed to an id nobody answers to, and a new org that ever took that id would
-- inherit them.
--
-- 0042 has run on boxes, so it is not edited: the constraint arrives here instead. NOT VALID,
-- because the table has rows and Postgres would otherwise read every one of them while holding a
-- lock the gateway's startup is waiting behind. It is enforced for every write from this
-- migration on; what is already there is checked by 0044, which a person runs.

ALTER TABLE agent_personas
    ADD CONSTRAINT agent_personas_org_fkey
    FOREIGN KEY (org) REFERENCES orgs (id) ON DELETE CASCADE NOT VALID;
