-- The networks an org asks 5060 to open to: its own PBX's, or a number it points at the box from a
-- carrier the box does not know. Nothing is admitted until the box's operator approves it; the
-- fence (channels/telephony/firewall.py) opens to the approved rows alone.
CREATE TABLE carrier_networks (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org text NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    -- What asked for it: a SIP peer's username, or the number hooked with networks of its own.
    source text NOT NULL,
    network cidr NOT NULL,
    state text NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'approved', 'refused')),
    asked_at timestamptz NOT NULL DEFAULT now(),
    decided_by text,
    decided_at timestamptz,
    UNIQUE (org, source, network)
);
CREATE INDEX carrier_networks_by_state ON carrier_networks (state, asked_at);

-- A number the org points at the box from a carrier of the box's catalog (carriers.csv): the
-- carrier's own networks fence it, whatever they are on the day of the call.
ALTER TABLE routes ADD COLUMN via text;
