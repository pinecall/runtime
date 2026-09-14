# A box in production

One machine that answers phone calls with an AI agent: the gateway, the worker, the media plane
and the database, installed from an operating system and nothing else.

Every command on this page was run against a real box, in this order, and **the outputs are what
came back** — including the refusals, which is the half you will actually meet. It was written by
deleting a working box's software and putting it back: the units, the app, the containers and the
box's own secrets, all gone, then this.

Writing a laptop's runtime instead? That is [from-zero.md](from-zero.md), which is the same
runtime with nothing in front of it. This page is the machine with a domain on it.

---

## What you need first

- **A machine** with 4 vCPU and 8 GB, on any provider that takes cloud-init. Debian or Ubuntu.
- **A domain** pointed at its IP. The box terminates TLS itself, with Caddy, so an `A` record is
  the whole of it.
- **One key of each provider role** — `llm`, `stt`, `tts`. `.env.example` is the list; a call
  needs one of each and nothing else.
- **Your checkout**, on your laptop. The deploy is rsync and ssh from it: nothing on the box ever
  clones, and the box needs no git, no GitHub and no registry.

Three ports, and no others: `22` for you, `80` and `443` for Caddy. The firewall in
`infra/box/nftables.conf` closes the rest, including Postgres and LiveKit — they answer on
loopback and nowhere else.

## 1. The machine, from cloud-init

`infra/box/cloud-init.yaml` is the whole of what has to exist before the first deploy can arrive:
the packages, the account rsync logs in as, the directory it lands in, uv, and the two values that
are this box's alone. Paste it as the instance's user-data — GCP's `user-data` metadata key, AWS's
User data, Hetzner's cloud config.

**Three lines are yours**, marked `YOURS`: your SSH public key, your domain, and the role.

```yaml
  - path: /etc/pinecall/box.env
    content: |
      PINECALL_DOMAIN=box.example.com            # YOURS
      LIVEKIT_PUBLIC_URL=wss://box.example.com   # YOURS: wss://<the same>
      PINECALL_ROLE=all                          # YOURS: all · hub · worker
```

Nothing there is a secret. **The secrets are drawn on the box itself**, by
`pinecall-secrets.service`, the first time it boots with code under it: the LiveKit keypair, the
database password, the operator key, the vault key. They are written as encrypted systemd
credentials, and no verb in this repository ever prints one.

> `/etc/pinecall/box.env` is the box's identity and the only file cloud-init writes that the
> deploy does not. Lose it and `make deploy` stops at `sed: can't read /etc/pinecall/box.env`,
> which is a true sentence about the wrong thing. Three lines, and it is back.

## 2. Point the deploy at it

On your laptop, beside the Makefile, `deploy.local.mk` — git-ignored, because the repository
describes a box and never which one is yours:

```make
BOX     = deploy@203.0.113.10
DOMAIN  = box.example.com
SSH_KEY = ~/.ssh/id_ed25519
```

## 3. Deploy

```bash
cd runtime
make deploy
```

`make deploy` is five steps, and it says each one as it runs it: **console** builds the browser
pages from the agents checkout beside this one · **sync** rsyncs two directories · **install**
puts every file of `infra/box/` where systemd reads it, installs the packages the box is missing,
enables the units this role owns and disables the others, and runs `uv sync` as the service user ·
**restart** makes the box's secrets, starts the media plane, restarts the gateway, and waits for
the domain to answer · **doctor** runs the runtime's own checks from inside the box.

```console
$ make deploy
scripts/console
console → src/pinecall/gateway/console (4 files)
admin → src/pinecall/gateway/admin (3 files)
…
sudo make -s -C /opt/pinecall/app/runtime/infra/box install
sudo systemctl start pinecall-secrets
sudo systemctl start pinecall-redis pinecall-livekit pinecall-sip pinecall-postgres
sudo systemctl restart pinecall-gateway pinecall-overflow
curl: (56) The requested URL returned error: 502
  not yet (1/10)
…
healthy: https://box.example.com
sudo systemctl restart pinecall-worker
active active active
```

The 502s are the gateway coming up behind Caddy; the health check knocks ten times and says which
attempt it is on. **`healthy:` is the line that means the domain answers.**

## 4. The doctor

The last step of every deploy, and the one to run on its own when something is wrong. It asks from
inside the box, with the box's own credentials — never yours.

```console
$ make doctor
✓ api keys              the api_keys table — `pinecall-runtime keys issue --org <slug>` mints one
✓ provider keys         llm ANTHROPIC_API_KEY, OPENAI_API_KEY · stt DEEPGRAM_API_KEY, … · tts ELEVEN_API_KEY, …
✓ provider keys answer  ANTHROPIC_API_KEY · OPENAI_API_KEY · SONIOX_API_KEY · DEEPGRAM_API_KEY · ELEVEN_API_KEY
✓ livekit               http://127.0.0.1:7880/ — HTTP 200
✓ postgres              postgresql://pinecall@127.0.0.1:5432/pinecall — vector, pg_textsearch
✓ embedder              tei · BAAI/bge-m3 — http://127.0.0.1:8081/info — HTTP 200
! lk                    not installed — brew install livekit-cli

all up
```

`✓` answered · `!` advice, something degraded that stops no call · `✗` broken, and the verdict
names the first one down. **`provider keys answer` knocks at every vendor with the key the box
holds**, so a dead key is caught here and not by a caller.

> **On an ARM box, TEI cannot start**: its CPU image has no arm64 build. Set
> `EMBED_PROVIDER=perplexity` with a `PERPLEXITY_API_KEY` and lookups embed over HTTP with no
> container at all. Without either, the embedder line is `!` and a lookup is skipped and said in
> the call's log — no call fails for it.

## 5. The keys the box did not draw

The box makes its own. **What you bring is the vendors'**, one at a time, on stdin, from your
checkout — never as an argument, because argv is in `ps` for every account on the machine:

```bash
printf '%s' "$ANTHROPIC_API_KEY" | make secret NAME=ANTHROPIC_API_KEY
make restart
```

Comparing two boxes, or checking one after a rotation, is by **fingerprint** and never by value:

```console
$ ssh $BOX 'sudo systemd-creds decrypt --name=ELEVEN_API_KEY \
    /etc/credstore.encrypted/ELEVEN_API_KEY - | shasum -a 256 | cut -c1-12'
b50420886f22
```

`e3b0c44298fc` is the sha256 of the empty string: a credential written from a variable that was
not set.

## 6. The first org and the first person

The schema seeds one org, `default`. One command turns a migrated database into a box somebody can
sign in to:

```console
$ ssh $BOX
$ sudo -u pinecall /opt/pinecall/venv/bin/pinecall-runtime init \
    --email you@example.com --person "Your Name"
org default is already there
m_b3796f3579fc  you@example.com  admin  runs this box
  https://box.example.com/invitations/inv_…

  Open the link above to set a password. Then, in the directory of an agent:

    pinecall login https://box.example.com
    pinecall run
```

`init` makes the org, invites its first **admin**, and makes that person an **operator** of this
box — somebody has to be able to make the second org, and on a fresh box there is nobody else.
Open the link, set a password, and the console is yours at `https://box.example.com`.

A second tenant is two more verbs:

```bash
pinecall-runtime orgs add clinica --name "Clínica Norte"
pinecall-runtime orgs invite clinica ana@clinica.uy --name "Ana" --role admin
pinecall-runtime orgs quota clinica --agents 5 --seats 10
```

## 7. Run Clínica Norte against it

From the **agents** checkout, on your laptop or on a server of the tenant's. Nothing of this runs
on the box: the agent is the tenant's own process, and it opens one outbound socket.

```console
$ cd agents/examples/clinica-norte && pnpm install
$ pinecall login https://box.example.com

open this to sign in:
https://box.example.com/cli?c=cli_…

waiting…
▸ box · https://box.example.com · org pinecall · sandbox

$ pinecall run
clinica-norte · pinecall · sandbox · connected to https://box.example.com · key from profile · tools 5 · doors phone +34910000000, whatsapp +34910000000, web
console  https://box.example.com/a/clinica-norte?login=lc_…   (opens within five minutes, once)
line     rings in this terminal
```

One line, and it says the four things that decide where you are: the agent, **whose org**, **which
world**, and where the key came from. Then, in another terminal:

```console
$ pinecall chat
‹ hola, quería una cita con el fisio
› Clínica Norte, buenos días. ¿En qué puedo ayudarle?
› Buenos días. Para buscar su cita necesito su nombre completo y un teléfono de contacto.
```

**That key opens the sandbox**, which is the world a person's login gives them. What answers your
customers is a key issued for a machine:

```bash
pinecall keys issue --label "the prod server" --scope app     # printed once
pinecall login --key-stdin https://box.example.com < the-key  # on that server
pinecall run --env production
```

`--env` asserts and never selects: a key opens one world, so the flag is you saying which one you
believe you hold, and the verb stops when the key disagrees. A person's key does not open `app` in
production at all — what holds a deployed slug is a key issued for a server, never a laptop that
logged in.

Every verb, with its own outputs: [from-zero.md](from-zero.md) and the agents repo's
`docs/the-cli.md`.

## 8. A phone number

The agent answers the web out of the box. A telephone needs a carrier — the tenant's own Twilio
account or a SIP peer — brought once, and then one number wired in three steps:

```bash
pinecall numbers import +34910000000 --agent clinica-norte --dry-run   # the plan, nothing written
pinecall numbers import +34910000000 --agent clinica-norte
```

`--dry-run` prints the very steps with the ids that stand today and writes nothing: the carrier's
trunk pointed at this box, the SFU's trunk admitting the number, the route. It is what you read
before letting the gateway touch a carrier account.

An org buys **one** number, and that is why there is no third world:

```bash
pinecall numbers move +34910000000 --env sandbox      # try the new agent on the real line
pinecall numbers move +34910000000 --env production   # and back
```

One row, in effect on the next call, carrier untouched. [protocol/numbers.md](protocol/numbers.md)
is the door and its refusals.

---

## What the box actually is

```
                 ┌─ caddy ────────── TLS, :80 :443, the only thing the internet reaches
internet ──────► │
                 └─ pinecall-gateway ── the API and the two pages, :8080 on loopback
                        │
                        ├── pinecall-postgres   the log, the orgs, the keys, the routes
                        ├── pinecall-livekit    the media plane, :7880 on loopback
                        ├── pinecall-sip        a telephone's way in
                        ├── pinecall-redis      a bus, not a store
                        └── pinecall-tei        what embeds, when it is this box's job
                 pinecall-worker ── answers a call with audio, dials the gateway by name
```

Every one is a systemd unit; the five containers are Quadlets. `infra/box/README.md` is the box
itself, credential by credential and unit by unit.

```bash
make status          # every unit and container, one line each
make logs UNIT=worker
make ssh
```

## When it does not come up

| you see | it means |
|---|---|
| `sed: can't read /etc/pinecall/box.env` | the box has no identity file. Three lines, §1 |
| `mkdir: cannot create directory '/opt/pinecall/app'` | it was deleted; the deploy remakes it now, older ones did not |
| `curl: (56) … 502` ten times, then `never answered` | the gateway did not start. `make logs` |
| `Error: parsing file ".../media.env"` | the box's own secrets are missing. `make deploy` starts `pinecall-secrets`; a reboot also does |
| `livekit — ConnectError: Connection refused` | the media plane is down. `make deploy` starts it; it never restarts one under a call |
| `✗ provider keys answer  ELEVEN_API_KEY refused (HTTP 401)` | a dead key, not a failed deploy. Rotate it, §5, then `make restart` |
| `no database: a key is verified against the api_keys table` | no `DATABASE_URL`, or the schema was never migrated |

**Nothing fixed by hand on a box counts.** A package goes in `PACKAGES`, a secret through
`make secret`, a class of failure into the doctor — and then the box re-converges through
`make deploy`. A box is a thing this repository can rebuild, or it is not a box.
