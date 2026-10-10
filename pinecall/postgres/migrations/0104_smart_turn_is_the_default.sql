-- Smart Turn v3 is the platform's end of turn wherever the ears do not end it themselves. The
-- providers row was written with every default spelled out, so the old default, livekit's v1-mini,
-- reads as each vendor's choice: it is taken out, and the code's default stands for it.
UPDATE box_settings
SET value = jsonb_set(value, '{tuning}', (
    SELECT jsonb_object_agg(
        key,
        CASE WHEN key LIKE 'stt/%' AND entry->>'turn_model' = 'v1-mini'
             THEN entry - 'turn_model'
             ELSE entry END
    )
    FROM jsonb_each(value->'tuning') AS tuning(key, entry)
))
WHERE name = 'providers' AND jsonb_typeof(value->'tuning') = 'object' AND value->'tuning' <> '{}'::jsonb;
