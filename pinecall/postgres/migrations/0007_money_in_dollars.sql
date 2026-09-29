-- Money in US dollars: what a call cost, and what an org may spend in a month.
--
-- Providers price in dollars, so the runtime stops converting to euros at a rate. Every euro
-- kept so far becomes the same number of dollars, never converted: a budget of 50 euros is $50,
-- and a call that cost 2 euros cost $2. A database taken over from v1 may carry both columns
-- already, the dollar one beside the euro one: there the dollars that are missing are copied
-- from the euros, and the euro column goes.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'call_facts'
                 AND column_name = 'cost_usd') THEN
        UPDATE call_facts SET cost_usd = cost_eur WHERE cost_usd IS NULL;
        ALTER TABLE call_facts DROP COLUMN cost_eur;
    ELSE
        ALTER TABLE call_facts RENAME COLUMN cost_eur TO cost_usd;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'quotas'
                 AND column_name = 'budget_usd') THEN
        UPDATE quotas SET budget_usd = budget_eur WHERE budget_usd IS NULL;
        ALTER TABLE quotas DROP COLUMN budget_eur;
    ELSE
        ALTER TABLE quotas RENAME COLUMN budget_eur TO budget_usd;
    END IF;
END $$;
