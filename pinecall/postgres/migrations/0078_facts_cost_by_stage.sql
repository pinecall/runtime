-- A call's cost by stage: what its model, ears, voice, phone legs and the box's own compute came
-- to, folded from the summary's rows beside cost_usd (log/facts.py), so a day's insights say which
-- stage an agent spends on without reading a summary. A call folded before this is filled in by
-- `pinecall-runtime facts rebuild`.
-- ADD COLUMN with no default and no NOT NULL changes the catalog alone: ACCESS EXCLUSIVE on
-- call_facts for the instant of the statement, no rewrite; a fold waits that instant.
ALTER TABLE call_facts
    ADD COLUMN cost_llm_usd double precision,
    ADD COLUMN cost_stt_usd double precision,
    ADD COLUMN cost_tts_usd double precision,
    ADD COLUMN cost_phone_usd double precision,
    ADD COLUMN cost_platform_usd double precision;
