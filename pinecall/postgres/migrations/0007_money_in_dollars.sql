-- Money in US dollars: what a call cost, and what an org may spend in a month.
--
-- Providers price in dollars, so the runtime stops converting to euros at a rate. Every euro
-- kept so far becomes the same number of dollars, never converted: a budget of 50 euros is $50,
-- and a call that cost 2 euros cost $2. Migrations run in the deploy, before the gateway
-- restarts, so the columns are renamed in place.

ALTER TABLE call_facts RENAME COLUMN cost_eur TO cost_usd;

ALTER TABLE quotas RENAME COLUMN budget_eur TO budget_usd;
