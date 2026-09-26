-- 0044, post-deployment: the personas written before 0043 checked against `orgs`.
--
-- Run by a person, `pinecall-runtime migrate up --post`, never at startup: VALIDATE reads every
-- row of the table, which is the pass 0043 left out on purpose so a deploy is not waiting behind
-- it. It takes a lock that lets reads and writes through, so the gateway keeps answering while it
-- runs. Until it runs, the constraint holds for every write since 0043 and the older rows are
-- simply unchecked — never wrong, only untested.
--
-- A row addressed to an org that is gone fails this, and that is the answer: the personas of an
-- org nobody deleted them for. Delete them by hand and run it again.

ALTER TABLE agent_personas VALIDATE CONSTRAINT agent_personas_org_fkey;
