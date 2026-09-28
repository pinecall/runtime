# Security

Report a vulnerability to security@pinecall.io. Say which door or unit is affected, how to
reproduce it against a box of your own (never against pinecall.io), and whether it exposes a
tenant's data. You get an answer within three working days and a fix or a plan within thirty.

Supported: the latest release tag. Secrets on a box are systemd credentials and every tenant
secret in the database is sealed under `PINECALL_VAULT_KEY`; `docs/the-environment.md` says
which variable holds what.
