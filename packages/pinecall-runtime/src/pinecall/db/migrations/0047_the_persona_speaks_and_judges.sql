-- 0047: a persona says who plays it, in what voice, and when it hangs up satisfied.
--
-- A synthetic caller was a goal, a manner and a handful of facts (0042), and the runtime chose
-- the rest: the model that improvised it was whichever the box runs by default, its voice one of
-- two ElevenLabs premades that are not the agent's, and its goal reached the improvising model's
-- prompt and nothing else — no judge ever asked whether the caller got what it came for.
--
-- Three columns say the first two the way the agent's own settings say them: `llm`, `tts` and
-- `voice` are the same three strings `pinecall agent set` takes, read by the same parser
-- (providers/tuning.py), refused at the door for a vendor this build has no file for. Two say the
-- third in the caller's own words: when it accepts the call, and when it declines it. The judge
-- that reads them is `persona`, on the hang-up panel (evals/judges/persona.py); the rule itself
-- rides the call's own `call.started`, so a call is judged by what the caller was when it was made
-- and not by what the row says by the time somebody re-judges it.
--
-- NULL on the three knobs is "unset": the runtime's default, which is what every caller was until
-- now. The two rules default to the empty string as `about` does, and an empty pair is a caller
-- nobody judges — every row already here. A constant default is written into the catalog and not
-- into the rows, so this holds no lock worth naming on a table of a handful of rows per org.

ALTER TABLE agent_personas
    ADD COLUMN IF NOT EXISTS llm           text,
    ADD COLUMN IF NOT EXISTS tts           text,
    ADD COLUMN IF NOT EXISTS voice         text,
    ADD COLUMN IF NOT EXISTS accepts_when  text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS declines_when text NOT NULL DEFAULT '';
