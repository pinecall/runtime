-- 0022: the pipeline door gets a `tts` knob, so the voice VENDOR can be turned like the other two.
--
-- Until here an operator could move an agent onto another STT vendor and another LLM vendor from
-- the console, and could change the voice and the voice model — but never the vendor that speaks.
-- That was invisible while this build ran one TTS vendor. It runs forty-five now
-- (providers/catalog.py), and a screen that offers Cartesia everywhere except the one stage that
-- actually speaks is a screen that lies.
--
-- The column reads like `stt` and `llm` do: `cartesia`, or `cartesia/sonic-3` — a bare vendor
-- keeps whatever model was already in use, and the model after the slash is the same thing
-- `tts_model` set. `tts_model` stays: it is what the old rows hold and what a person who only
-- wants to change the model still types, and providers/overrides.py reads both.
--
-- NULL is a knob nobody turned, exactly as every other column here. Nothing is backfilled: an
-- agent that was never turned onto another voice vendor was running its class's, and still is.

ALTER TABLE pipeline_overrides ADD COLUMN IF NOT EXISTS tts text;
