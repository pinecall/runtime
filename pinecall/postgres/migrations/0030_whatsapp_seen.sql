-- A WhatsApp message is read once: Meta delivers a webhook again when it thinks it unanswered.
-- The gateway claims a message by inserting its row before reading it (channels/whatsapp.py), so
-- of two deliveries at once, in one process or two, only one reads it; `read_at` is set once it is
-- queued or kept, and a claim still unread past its lease is taken by the next delivery. Keyed by
-- org, so one org's message id never shadows another's. The nightly retention run forgets the rows
-- past Meta's 7 days of retries, by `claimed_at`.
-- A new table: CREATE TABLE locks nothing that exists but `orgs`, in SHARE ROW EXCLUSIVE for the
-- foreign key, for the instant of the statement: reads of orgs go on, a write to it waits that long.
CREATE TABLE whatsapp_seen (
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    message_id text NOT NULL,
    claimed_at timestamptz NOT NULL,
    read_at timestamptz,
    PRIMARY KEY (org, message_id)
);

CREATE INDEX whatsapp_seen_claimed_at ON whatsapp_seen (claimed_at);
