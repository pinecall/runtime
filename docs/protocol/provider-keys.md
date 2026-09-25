# Provider keys, and the vault

Section 6 of [gateway-api.md](gateway-api.md), on a page of its own: an org's own vendor keys,
where they are kept, and the one door that ever reads one back.

By default every call runs on **the box's own vendor keys**, out of its environment. An org may
bring its own account instead:

| | |
|---|---|
| `PUT /v1/provider-keys/{vendor}` | `{"key": "sk-…"}` — this org's own account with that vendor. Every call of the org runs on it from the next one |
| `DELETE /v1/provider-keys/{vendor}` | give that vendor back to the box's key |
| `GET /v1/provider-keys` | `{"vendors": ["elevenlabs", …]}` — **names only** |

No door a person reads answers with a provider key: not a value, not a prefix, not a fingerprint.
**One door does read them back** — `GET /v1/agents/{slug}/provider-keys` answers `{"keys": {vendor:
key}, "lends": […] | null}` in the clear — the keys, and which of the box's the org is lent
([limits.md](../limits.md)) — and it is the worker's: an org's own keys, to the org's own process, on the
org's own key, so a spoken call runs on the account the tenant brought. It is the whole reason the
vault exists, and a tenant's own code may call it for the same reason. A key that was lost is set
again.

The row is encrypted at rest with **Fernet**, under `PINECALL_VAULT_KEY` — one secret, generated
once on the box, that lives in the environment and never in the database, so a stolen dump is not
a stolen tenant. A runtime with no `PINECALL_VAULT_KEY` **cannot keep somebody else's secret and
says so**: these three doors answer

```
503 no PINECALL_VAULT_KEY: this runtime cannot keep a tenant's key
```

and every call runs on the box's own keys, which is a complete self-hosted install. To turn the
doors on: `pinecall-runtime box secrets` generates one (32 random bytes, url-safe base64) among
the seven a fresh box is made of; on a laptop, put one line in the runtime's `.env` and restart
the gateway. Rotating it is not a migration the runtime does for you: the rows are unreadable
under a new key, so tenants set their keys again.
