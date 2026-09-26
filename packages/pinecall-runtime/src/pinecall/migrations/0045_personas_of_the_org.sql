-- 0045: a persona belongs to the ORG, not to one of its agents.
--
-- 0042 filed a synthetic caller under one agent, `(org, agent, name)`, because the file it came
-- from sat in that agent's directory. But a caller is a PERSON ON THE PHONE: who they are — what
-- they want, how they talk, the facts they may state — does not depend on which of the org's
-- agents picks up. Filing them per agent meant `price-shopper` was written once for sales and
-- again for dispatch, the doors had to name an agent that had nothing to do with the answer, and
-- the console had to group one list by a thing that was not a property of it. One list per org.
--
-- THE COLLISION. Until this runs, two agents of one org may each hold a name, and `(org, name)`
-- cannot take both. Nothing is deleted: the most-recently-written row — `set_at DESC`, and the
-- agent's own name to break a tie — KEEPS the name, and every other row of that name is renamed
-- to `<name>-<agent>`, which is a persona name a person can read, call with and delete from the
-- console. It repeats until no name is held twice, because `<name>-<agent>` may itself be a name
-- somebody already wrote. Production was read before this was written (2026-09-20: cloudacio's
-- two agents and maravilla's one hold five disjoint names between them), so on the box this loop
-- renames nothing — it is here because a merge between then and the deploy is free to add one.
--
-- `agent` is NOT dropped. A column is dropped in two migrations and the code stops using it
-- first, which is what this one does; it only loses its NOT NULL so a write that no longer names
-- an agent is a write this table takes.

DO $$
BEGIN
    LOOP
        WITH ranked AS (
            SELECT org, agent, name,
                   row_number() OVER (PARTITION BY org, name ORDER BY set_at DESC, agent) AS rank
              FROM agent_personas
        )
        UPDATE agent_personas AS held
           SET name = held.name || '-' || held.agent
          FROM ranked
         WHERE ranked.rank > 1
           AND ranked.org = held.org
           AND ranked.agent = held.agent
           AND ranked.name = held.name;
        EXIT WHEN NOT FOUND;
    END LOOP;
END $$;

ALTER TABLE agent_personas DROP CONSTRAINT agent_personas_pkey;

-- After the old key and not before it: Postgres refuses to drop a NOT NULL while the column is in
-- a primary key. Nobody writes this column any more — the doors and orgs/personas.py stopped
-- naming an agent in the same commit — and a NOT NULL with no default would refuse every write
-- that follows.
-- squawk-ignore ban-drop-not-null
ALTER TABLE agent_personas ALTER COLUMN agent DROP NOT NULL;

-- The table is a handful of rows per org — a team writes callers by hand — so the ACCESS
-- EXCLUSIVE lock and the unique index this primary key builds take milliseconds, well inside the
-- five seconds a startup migration is held to, and there is no NOT VALID for a primary key to be
-- split into. An index on a big table would have been a `.post.sql`; this is not one.
-- squawk-ignore constraint-missing-not-valid, adding-serial-primary-key-field
ALTER TABLE agent_personas ADD PRIMARY KEY (org, name);
