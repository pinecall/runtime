-- 0034: what each org may dial out, and how often.
--
-- A box that can place calls is a box worth stealing. The SIP fraud that pays is international
-- revenue share: somebody reaches a trunk, dials a premium range abroad all night, and collects
-- a cut of the termination. This box has been probed on 5060 before, which is why the fence
-- (infra/box/nftables.conf) opens it to the carrier alone — but a DIAL door is a way in that the
-- fence cannot see, because the request arrives at 443 with a key on it.
--
-- So the guards are here, per org, and every one of them is on by default. `dial_anywhere` off
-- means a destination must already have called or written to one of this org's agents: a call
-- BACK goes back to somebody, and that single rule is what makes a stolen key worth nothing to a
-- fraudster — they would have to call in first, from the number they meant to bill.
--
-- It is NOT a quota (types/org.py: a quota is a count of a thing the org holds or has consumed,
-- and the protocol's credits.exhausted names the eight by hand). These are a rate, a fence and a
-- ceiling, so they keep their own row, their own operator door and their own refusals.
--
-- NULL on any column is "nobody said", and the code's own defaults stand — six dials a minute,
-- two hundred a day, ten minutes a call. A row that exists only to set dial_anywhere still dials
-- at six a minute. Zero is a real limit and refuses everything, as a quota's zero does.

CREATE TABLE IF NOT EXISTS dial_policy (
    org             text PRIMARY KEY REFERENCES orgs (id) ON DELETE CASCADE,
    -- The operator's own switch: off, only a number that has already reached this org. It is
    -- here and not on a tenant door on purpose — an org that could lift its own fence has none.
    dial_anywhere   boolean,
    per_minute      integer,
    per_day         integer,
    -- The country calling codes this org may reach, as digits: {'34','1'}. Empty or NULL is not
    -- "anywhere" — it means the codes of the org's OWN numbers, worked out at each dial, so a
    -- tenant that imports a Spanish number can dial Spain and nothing else without being asked.
    countries       text[],
    max_duration_s  integer,
    set_at          timestamptz NOT NULL DEFAULT now()
);
