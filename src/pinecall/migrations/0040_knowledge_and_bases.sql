-- 0040: the bases an agent reads are `bases`, and `knowledge` is what it knows by heart.
--
-- 0037 kept the bases a world attached under the key `knowledge` in the settings' jsonb. That word
-- means the other thing now — the business as the org describes it, in Markdown, read whole into
-- the static block of every call, set by the floor without a deploy — and the RAG is `bases`.
-- Every settings row that names bases is rewritten under the new key; a row without the key is
-- left as it was. Milliseconds: one row per version per corner per agent.
--
-- And `pipeline_overrides` goes. 0037 copied its rows into agent_config and nothing has read the
-- table since — the six-knob door wrote through the settings store for one release, and that door
-- is gone with this migration. Two migrations, the code first: this is the second.

UPDATE agent_config
   SET config = (config - 'knowledge') || jsonb_build_object('bases', config -> 'knowledge')
 WHERE config ? 'knowledge' AND jsonb_typeof(config -> 'knowledge') = 'array';

-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS pipeline_overrides;
