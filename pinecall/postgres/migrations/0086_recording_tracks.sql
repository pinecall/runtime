-- A call's recording is one file per track of its room (the caller, the agent, the hold melody, a
-- supervisor who took over, the far end of a transfer): livekit's egress copies each track's Opus
-- into an Ogg as it was sent, and the box's gateway seals it under the call's key when egress says
-- it ended (gateway/ending/recorded.py). One row per track that landed: what it is, when it began
-- and ended, and the sealed object's name in the call's directory, so the door mixes them in time.
-- Two gateways may each take one track of a call at once: a row each, no file to race over. An
-- erasure deletes the rows with the call. A new table with no foreign key: no lock on any other.
CREATE TABLE recording_tracks (
    call text NOT NULL,
    name text NOT NULL,
    kind text NOT NULL,
    started_at double precision NOT NULL,
    ended_at double precision NOT NULL,
    PRIMARY KEY (call, name)
);
