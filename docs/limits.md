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
                                    --knowledge-chunks 20000 --numbers 1 --seats 10 \
                                    --llm-tokens 2000000
```

The whole set is replaced at once, and a limit left out is **no limit**. The meter is a fold over
the log — there is no counter table to drift — and the gate runs before a call opens, before an
agent registers, before memory writes a fact, and before an invitation makes a row: `seats` is
what a plan sells a team by, counted as everybody the org has not disabled. A tenant over one is refused with a sentence and
`credits.exhausted` in their own log.

A call opened with minutes left does not outrun them: the open door answers the worker how many
seconds are left, and the call is kept to the lesser of that and the agent's own `max_duration_s`
on the one clock that ends it — the agent told to close a minute before, the call ended as
`timeout` by the `platform`, a written visit in the browser included. When it is the org's minutes
that end it and not the agent's own limit, `credits.exhausted` is written on the call's log just
before `call.ended`, so the call says why it ended. Calls open at once each
count from the same minutes, so `concurrent_calls` is what keeps a trial from spending them twice;
the next call is refused with `credits.exhausted`.

`llm_tokens` counts what the org's models read and wrote, input and output together, as each
`call.summary` reports them — the box's keys and the org's own alike. A **written** conversation
is held to it, and to `messages`, on **every turn** and not only when it opens: the meter folds a
call when it hangs up, so the turn is asked with what the open conversation has spent so far added
to the org's totals. Past either, that turn is not answered: the call ends as `timeout` by the
`platform`, `credits.exhausted` lands in the agent's log, the chat socket closes with the sentence
(`org tienda has used 2000140 of its 2000000 llm_tokens: credits.exhausted`), a WhatsApp thread
closes answering nothing, and a run of text goldens (`pinecall test`) stops there as `failed` with
the sentence, the goldens scored before it kept. A voice call is held to them when it opens.

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

## What an org can see

`GET /v1/limits`, with any key of the org, answers every quota as `{limit, used}` — counted where
the gate counts it, so the page and the refusal read one number — with `lends` and `billing_url`
(`PINECALL_BILLING_URL`: where the box's orgs pay; `null` on a box that bills nobody). The console
draws a meter from it on Home and Usage **only where `minutes` has a limit**, and an upgrade link
only where `billing_url` is set: a self-hosted box shows neither.

## What a new org is allowed

A new org's quotas are a policy's to say, not the operator's to type. A package named in
`PINECALL_EXTENSIONS` answers `admitted(org, email, world, already)` — `already` the orgs that
email already belongs to on this instance, so a trial can be one per person — with the org's `Quotas`, and each
instance asks it **once, in its own world**, the moment it makes an org it did not have: production
at signup, a sandbox the first time it mirrors the org from production. An org already there — a
later sign-in, one the sandbox seed copied — is never admitted again. With no package loaded the
answer is no limit and no row, which is what a self-hosted box runs.
