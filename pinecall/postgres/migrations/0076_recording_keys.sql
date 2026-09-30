-- A recording is sealed where it is kept: the worker seals the file under a key of the call's own
-- before it stores it on the disk or in the bucket, and the gateway opens a player's range with
-- it (process/sealed_audio.py). The key is kept here, sealed under PINECALL_VAULT_KEY, one row per
-- call, made the first time the call's worker asks (tenancy/recording_keys.py); `vault rotate`
-- re-seals it, and an erasure deletes it with the call, so a copy of the file that outlives the
-- erasure (a night's backup) no longer opens. A recording stored before this is plain, and named so.
-- A new table with no foreign key: CREATE TABLE takes no lock on any table that exists.
CREATE TABLE recording_keys (
    call text PRIMARY KEY,
    org text NOT NULL,
    sealed text NOT NULL,
    made_at timestamptz NOT NULL DEFAULT now()
);
