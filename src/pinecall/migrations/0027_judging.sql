-- 0027: whether an org's calls are judged at hang-up.
--
-- Judging a call at hang-up may ask a model, and a model is billed: an org may decline it. The
-- setting is on the org's own row because it is about the org and not about one agent, and it is
-- NULL for every org that never said — which reads as on, as every org was judged before this
-- migration. A call its org did not judge can still be judged later, on somebody's ask
-- (POST /v1/evals/judge/{call}).

ALTER TABLE orgs ADD COLUMN IF NOT EXISTS judging boolean;
