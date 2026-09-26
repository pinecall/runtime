-- 0051: how many LLM tokens an org's calls may spend, input and output together.

-- NULL is no limit, which every org nobody limited has. It is a count over the org's whole life,
-- folded from call.summary like minutes, and a written conversation is held to it on every turn
-- (orgs/admission.py). bigint: a paying org's lifetime of tokens passes what an integer holds.

ALTER TABLE quotas ADD COLUMN IF NOT EXISTS llm_tokens bigint;
