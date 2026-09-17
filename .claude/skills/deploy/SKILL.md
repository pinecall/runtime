---
name: deploy
description: Deploy this checkout to a box (hub, worker, or one machine) and verify it, without a secret ever reaching a screen. Use for make deploy, a second box, rotating a vendor key, or when the doctor refuses a key.
---

# Deploying a box

The deploy is the root `Makefile`: rsync + ssh + make + curl, from a checkout, to the account
cloud-init made. Nothing on the box can clone; nothing here uses shipway. The box's identity is
`deploy.local.mk` beside the Makefile (git-ignored): `BOX`, `DOMAIN`, optional `SSH_KEY`.

## NEVER

- Print a secret: not a credential's value, not a key from the shell, not a line of a journal
  that carries one. Compare credentials by fingerprint (below). Pipe every journal read through
  `sed -E 's/(key|secret|token)[=:] *[^ ]+/\1=***/Ig'`.
- Fix the box by hand and stop there. An `apt install`, a credential rewritten with
  `systemd-creds`, a unit edited in `/etc`: each is at most a probe. The same fix goes in the
  tree (`PACKAGES` in `infra/box/Makefile`, `make secret`, a doctor check, the unit file) and the
  box re-converges through `make deploy` before the turn ends.
- Restart a container (`pinecall-livekit`, `-sip`, `-postgres`, `-redis`) during a call. The
  deploy never does; a changed `.container` is restarted between two calls, by a person.
- Run `shipway deploy` anywhere near this tree. `~/pinecall/v2/shipway.yml` points at the same box.
- Leave `PINECALL_API_KEY` exported while running the deploy or anything that opens a socket:
  `unset PINECALL_API_KEY` first.

## The path

```bash
unset PINECALL_API_KEY
make deploy                                  # sync → install → restart → doctor
make deploy BOX=deploy@<ip> DOMAIN=<domain>   # another box, same tree
```

What each step proves: `sync` — the two directories the box needs; `install` — every file of
`infra/box/` where systemd reads it, the packages the box is missing, the role's units enabled and
the others' disabled, `uv sync --frozen` as the service user; `restart` — by role: `all` restarts
gateway → health through Caddy → worker; `hub` restarts the gateway only (`systemctl restart`
would START a worker the role disabled); `worker` restarts its one unit; `doctor` — the runtime's
doctor as the units see the box, every vendor knocked with the key the box holds.

A deploy that ends in `✗ provider keys answer  refused ELEVEN_API_KEY (HTTP 401)` is a dead key,
not a deploy failure: rotate it (below), `make restart`, `make doctor`.

## Fingerprints, never values

```bash
ssh <box> "sudo systemd-creds decrypt --name=$N /etc/credstore.encrypted/$N - | sha256sum | cut -c1-12; \
           sudo systemd-creds decrypt --name=$N /etc/credstore.encrypted/$N - | wc -c"
```

Two boxes agree when the twelve characters and the byte count agree. `e3b0c44298fc` is the
sha256 of the EMPTY string: a credential written from an unset shell variable. The shell may call
a key differently from the runtime (`ELEVENLABS_API_KEY` vs `ELEVEN_API_KEY`); `.env.example`
is the runtime's list.

## Rotating a vendor key

```bash
printf '%s' "$ELEVENLABS_API_KEY" | make secret NAME=ELEVEN_API_KEY   # the value on stdin, on no screen
make restart && make doctor
make worker-secrets WORKER=deploy@<worker-ip>                          # every worker takes the hub's copy
```

`box secrets` (the generated ones: LiveKit pair, Postgres, ops, vault) never rotates; `box secret
<NAME>` replaces in place.

## The box's mail

`PINECALL_SMTP_URL` is a secret (`make secret`), `PINECALL_MAIL_FROM` is a line of
`/etc/pinecall/box.env`; with either missing the box sends nothing and the doctor's `mail` line is
`!`, never the verdict. Three traps, each a refused letter and not an error at startup:

- **SES's SMTP password is not the IAM secret access key.** It is derived from it and the region,
  so it is region-specific; a secret key pasted in its place is `535 Authentication Credentials
  Invalid`. The SES console's *Create SMTP credentials* derives it; AWS's *Obtaining Amazon SES
  SMTP credentials* page is the algorithm. Build the URL with `read -rs` and `printf … | make
  secret`, never on argv. `/` and `+` in it may be raw or percent-encoded.
- **A sandboxed SES account sends only to verified addresses**: `554 Message rejected: Email address
  is not verified` until production access is granted. The sender's domain must be a verified
  identity with DKIM.
- **Prove it with a letter, not with the line**: `make doctor MAIL_TO=you@…` posts one and prints
  what the server said. An org's own account is proved at `POST /v1/org/mail/test`, and its last
  refusal is `last_error` at `GET /v1/org/mail`.

## A second box

The hub keeps the gateway and the media plane (`PINECALL_ROLE=hub` in its `/etc/pinecall/box.env`);
each worker is `PINECALL_ROLE=worker` with `LIVEKIT_URL=wss://<hub-domain>`,
`PINECALL_GATEWAY_URL=https://<hub-domain>`, `PINECALL_MAX_JOBS=<measured>` — cloud-init writes
those lines. Order: `make worker-secrets WORKER=…` · `make deploy BOX=<worker> DOMAIN=<hub>` · the
hub's SFU log says `worker registered` with a new `AW_` id · one voice call lands there (the
`debug-a-call` skill) · only then the hub's role flips to `hub` and `make deploy` runs on it.

## Reading the box

```bash
make status                                   # every unit and container, one line each
make logs UNIT=worker                         # gateway (default) · worker · caddy
ssh <box> 'sudo podman logs --since 5m pinecall-livekit 2>&1 | grep -E "worker registered|job"'
ssh <box> "sudo journalctl -u pinecall-worker --since '$START' -o cat" | sed -E 's/(key|secret|token)[=:] *[^ ]+/\1=***/Ig'
```

## Gotchas

- A fresh box has `/opt/pinecall/app` and nothing under it; `sync` makes the two directories.
- The fence lives in `prerouting` (raw priority) because DNAT'd container traffic never hits
  `input`, and it must accept udp/tcp 53 from `podman*` or containers cannot resolve each other.
- A credential file is named EXACTLY as the credential, no extension; `ImportCredential=` accepts
  a trailing glob only (`PINECALL_*`).
- The units run `/opt/pinecall/venv/bin/pinecall-runtime`, never `uv run`.
- `livekit` on the hub reads a worker's load every 0.5 s and stops routing at 0.7 of it:
  `PINECALL_MAX_JOBS` is one under the measured ceiling.
