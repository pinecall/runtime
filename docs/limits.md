# Limits: what an org may use, and whose keys it runs on

An org's limits are mechanisms the runtime enforces and never a plan: quotas it counts against the
log, and the vendor keys a call runs on — the org's own, or the box's. Who sets them is the
operator, or a policy a package beside the runtime plugs in ([ARCHITECTURE.md](../ARCHITECTURE.md)
§12, `extensions/`). With nothing set and nothing loaded, an org has no limit and runs on the box's
keys, which is what a self-hosted box wants. To charge for a box — plans, a trial, invoices — see
[charging-for-it.md](charging-for-it.md): the billing layer beside the runtime, and its contract.

## Provider keys, per tenant

By default every call runs on the **box's** vendor keys, out of its environment. An org may bring
its own, and then every call of that org runs on its account from the next one:

```bash
# the operator, for a tenant who sent theirs
printf %s "$KEY" | pinecall-runtime orgs provider-key set clinica elevenlabs
pinecall-runtime orgs provider-key list clinica

# or the tenant themselves, with their own org key (the `providers` scope)
pinecall providers add elevenlabs   # reads the key from stdin, never from a flag
```

The rows are encrypted with `PINECALL_VAULT_KEY`, which lives in the box's environment and never in
the database. A runtime without one cannot keep somebody else's secret and says so with a 503 —
[the gateway API §6](protocol/provider-keys.md).

## Quotas

```bash
pinecall-runtime orgs quota clinica --minutes 2000 --messages 5000 --agents 5 \
                                    --concurrent-calls 10 --memory-facts 50000 \
                                    --knowledge-chunks 20000 --numbers 1 --seats 10
```

The whole set is replaced at once, and a limit left out is **no limit**. The meter is a fold over
the log — there is no counter table to drift — and the gate runs before a call opens, before an
agent registers, before memory writes a fact, and before an invitation makes a row: `seats` is
what a plan sells a team by, counted as everybody the org has not disabled. A tenant over one is refused with a sentence and
`credits.exhausted` in their own log; nothing is cut mid-call.

## What the box lends

Where an org brought no key of its own for a vendor, its calls run on the box's — and the box
lends only what the org's quotas row says, in `lends`: **null** (no row, or nothing said) lends
every key the box has, which is what a self-hosted box and every org nobody limited run on; an
**empty list** lends nothing, so the org runs on its own keys alone; otherwise each entry is a
**vendor** (`deepgram`: every model of it) or **`vendor/model`** (`anthropic/claude-haiku-4-5`:
that model and its dated snapshots, read as a prefix — never `claude-sonnet-5`).

```bash
pinecall-runtime orgs quota tienda --minutes 30 \
    --lends deepgram,cartesia,anthropic/claude-haiku-4-5,openai/gpt-5.4-mini,openai/gpt-5.4-nano
pinecall-runtime orgs quota tienda --lends none      # its own keys only
```

It is read where a call's vendors are built (`providers/registry.py`, one place for the llm, the
ears and the voice, the text path and every simulation included), against the model that will
**run** — the one named, else the vendor's default — so a call is refused before it opens, never
mid-turn, with the sentence that names the fix: `anthropic/claude-opus-5 is not lent to this org:
it may run on …, or on a key of its own (pinecall providers add anthropic)`. The settings door
says the same `422` when the model is picked. An org's own key is never refused, whatever the
model. Judges and embeddings run on the box's keys regardless: judging is the org's to turn off,
and an embedder is the box's by construction.

## What a new org is allowed

A new org's quotas are a policy's to say, not the operator's to type. A package named in
`PINECALL_EXTENSIONS` answers `admitted(org, email, world)` with the org's `Quotas`, and each
instance asks it **once, in its own world**, the moment it makes an org it did not have: production
at signup, a sandbox the first time it mirrors the org from production. An org already there — a
later sign-in, one the sandbox seed copied — is never admitted again. With no package loaded the
answer is no limit and no row, which is what a self-hosted box runs.
