# `pinecall-runtime`

The operator's terminal: the two processes, the database, the tenants and the box. One module per
group, one parser each; `pinecall-runtime` alone prints them all, `<group> --help` that group's
verbs. The **tenant's** is `pinecall`, the agents repo's `docs/the-cli.md`, and they never overlap:
nothing here writes an agent, nothing there issues a key. Whose a key is: [multi-tenancy.md](multi-tenancy.md).

```bash
uv run pinecall-runtime <group> <verb>     # in a checkout
pinecall-runtime <group> <verb>            # installed, or inside the box's venv
```

## What each group needs

Three different things, and knowing which is which saves an afternoon:

| group | speaks to |
|---|---|
| `gateway` · `worker` · `box` · `doctor` · `providers` | this machine (`box peer` also an instance's gateway, on its ops key) |
| `migrate` · `sessions` | **Postgres**, straight, over `DATABASE_URL` |
| `init` · `orgs` · `keys` · `routes` · `fleet` | **a running gateway**, over `/v1/ops/*` with `PINECALL_OPS_KEY` — and `fleet loop`, a cloud's own CLI beside it |

So `keys issue` on a box whose gateway is down is refused by the client, not by the table, and
`sessions list` works whether or not anything is running.

## `init`

```
pinecall-runtime init [--org <slug>] --email <address> --person "<name>" [--name "…"] [--role admin]
```

The first org and the first person, on a runtime nobody has used yet — the one command between a
migrated database and a terminal that can sign in. It makes the org (`--org` defaults to
`default`, which the schema seeds), invites its first **admin**, makes that person an **operator**
of this box — somebody has to be able to make the second org, and on a fresh runtime there is
nobody else — and prints the invitation link and the two lines to type next.

```console
$ pinecall-runtime init --email you@example.com --person "Your Name"
org default is already there
m_b3796f3579fc  you@example.com  admin  runs this box
  http://127.0.0.1:8080/invitations/inv_…

  Open the link above to set a password. Then, in the directory of an agent:

    pinecall login http://127.0.0.1:8080
    pinecall link
    pinecall start
```

Run twice, it carries on to the person, not stopping at the org. A second tenant is [`orgs`](#orgs).

## `gateway`

```
pinecall-runtime gateway [--host 127.0.0.1] [--port 8080] [--reload]
```

The control plane: HTTP and WebSocket, one process, the whole API of
[protocol/gateway-api.md](protocol/gateway-api.md). With neither flag it binds the host and port of
its own `PINECALL_GATEWAY_URL` — `http://127.0.0.1:8080` unless the instance says otherwise — so an
instance's address is one variable, the one its worker asks too; a URL that is not loopback or
names no port is refused in one sentence, and a flag wins over its half of the URL. It needs, at the least, one key of each provider
role and `DATABASE_URL` with the schema applied — a laptop as much as a box, because a key is
verified against the `api_keys` table and there is nowhere else it could be. With no database the
gateway says so at startup and answers every keyed door 503 with the same sentence. `--reload`
restarts on a source change, for writing the runtime, not for running it.

## `worker`

```
pinecall-runtime worker [dev | start | overflow | talk | download-files] [flags…]
```

The fleet: the process that holds spoken calls. `dev` for a laptop, `start` for a box, `talk` to
join a room from this terminal, `download-files` to warm the model files a first call would
otherwise wait for. Everything after the verb goes to livekit's own CLI untouched. A worker takes
jobs off LiveKit and asks the gateway for everything else, so it needs `LIVEKIT_URL` and the pair,
`PINECALL_GATEWAY_URL`, and a key to knock with. `PINECALL_MAX_JOBS` caps how many calls one machine
holds; unset, it gates on CPU. `dev` and `start` also **heartbeat** to the gateway every five
seconds — the worker's name (`PINECALL_WORKER_NAME`, else the short hostname), the calls it holds,
its measured seats, its load — what `fleet list` shows and the loop sizes on. A worker told it was
**cordoned** takes no new call, finishes what it holds, and exits **3**; the unit's
`RestartPreventExitStatus=3` leaves it down. `overflow` is the one worker that is never full: it
runs on the hub, reports itself full to LiveKit until the gateway says every real worker is, and
then answers the call nobody else can — one sentence (`PINECALL_OVERFLOW_SAYS`), the caller's number
onto the agent's log as `callback.requested`, and it hangs up. No STT, no model.

## `sessions`

```
pinecall-runtime sessions list [--agent <slug>] [--limit 20]
pinecall-runtime sessions show <call-id> [--json]
pinecall-runtime sessions tail [<call-id>]
pinecall-runtime sessions recording <call-id>
```

The log, read back straight from Postgres — no gateway, no key, no org filter: this is the
operator's view of the box, and it sees every tenant's calls. `show` prints one call entry by
entry; `--json` prints the reduced state instead. `tail` follows a call as it happens, and with no
id it follows the newest live one. `recording` says where that call's audio was written — and, for a written (chat) call, which keeps none, says so (`call … was not recorded: its call.summary carries no path`) and exits 1.

## `orgs`

```
pinecall-runtime orgs list
pinecall-runtime orgs add <slug> [--name "…"]
pinecall-runtime orgs invite <org> <email> --name "…" [--role admin|manager|developer|supervisor|qa]
pinecall-runtime orgs operator <org> <email> [--revoke]  ·  orgs remove-member <org> <email>
pinecall-runtime orgs move <agent> <org>
pinecall-runtime orgs rm <org>
pinecall-runtime orgs quota <org> [--minutes n] [--messages n] [--agents n]
                                  [--concurrent-calls n] [--memory-facts n] [--knowledge-chunks n]
                                  [--numbers n] [--seats n] [--budget-eur n]
pinecall-runtime orgs dialling <org> [--dial-anywhere | --no-dial-anywhere]
                                     [--per-minute n] [--per-day n] [--max-duration-s n]
pinecall-runtime orgs sso <org> [--off]
pinecall-runtime orgs provider-key set <org> <vendor>     # the key on stdin
pinecall-runtime orgs provider-key rm  <org> <vendor>
pinecall-runtime orgs provider-key list <org>
```

The tenants. `<org>` is an id or a slug — every door takes either. `add` makes the row people will
type; `invite` is how a tenant gets its first person on a gateway that takes no sign-up — it prints
the row and a **link**, once, that opens the console's password card (the operator holds a token and
never a password, and the invitation takes none of the org's seats). The door it knocks at, `POST
/v1/ops/orgs/{org}/members`, also **mails** that link to the person when the box or the org has a
mailbox ([the-box.md](protocol/the-box.md)); the verb prints the link either way and does not say
whether a letter went. `rm` is refused while the org still has keys or routes, so a tenant is never
half-deleted. A person is their email, with one password across every org: `invite` of an address
that already has one prints no link and seats them `active` (`already a person on this box: seated,
they sign in with the password they have`). `operator` makes a member, by email, an operator of this
box — their key then opens every `/v1/ops` door, as `init` does for the first person; `--revoke`
takes it back. `remove-member` takes a person out of an org **for good** — keys revoked, row and
links gone, the seat free — and carries the door's refusal for the org's last active admin. `move`
undoes the one thing a slug could not undo: it belongs to the org that first registered it for as
long as its log exists, and a box walks into the wrong one by construction — its own worker and
operator keys are issued into `default`, so the first agent anybody runs there lands in `default`
too. The agent's own log, one head row per call it has taken, and its numbers all go with it; it is
refused while somebody is holding the slug, and a number the destination org already answers at
stays where it is and is named.

`dialling` replaces **the whole set** of what an org may dial out — `dial_anywhere` (off unless
said: a destination must already have called or written to one of the org's agents), dials a minute
(6), a day (200), and the longest a placed call may run (600 s); a guard left out goes back to the
code's default. Which countries a dial may reach is the carrier account's own setting, not a flag
here. `sso` prints which identity provider the org is wired to; `--off` lets its people sign in with
a password again while their provider is down, and is the one SSO thing the operator does — wiring
one is the org's own door ([protocol/people.md](protocol/people.md)). `quota` replaces **the whole
set**: a limit left out is no limit. The meter is a fold over the log, so there is no counter to
drift, and the gate runs before a call opens, before an agent registers, before memory keeps a fact
and before an invitation makes a row — never mid-call. `--seats` is what a plan sells a team by:
everybody the org has not disabled, invited and active together, because an invitation sent is a
seat taken. `--budget-eur` is euros a calendar month, set with the quotas and not one of them: shown
beside what was spent, and nothing is refused over it.

`provider-key set` reads the key from **stdin**, never from a flag, for the reason every verb in
this repo that touches a secret does: argv is visible in `ps` to every user on the box. The row is
encrypted under `PINECALL_VAULT_KEY`; without one this verb is refused with a sentence.

## `keys`

```
pinecall-runtime keys issue [--org <org>] [--label "…"] [--env production|sandbox]
                            [--scope <scope>]… [--subject <member>] [--name "…"]
pinecall-runtime keys list  [--org <org>]
pinecall-runtime keys revoke <fingerprint>
```

```console
$ pinecall-runtime keys issue --org clinica --label "the ci job"
pc_live_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
  org clinica · production · the ci job
  every scope
  copy it now: the table keeps the fingerprint, and the key is never shown again
```

That sentence is the whole group: the table stores a sha256 and **no verb anywhere reads a key
back**. `list` prints fingerprint, world, label, whose, and whether it is revoked. `revoke` stops
honouring the key whose fingerprint `list` printed; the row and history stay, so log entries naming
it remain readable. Issue one key per place — a laptop, CI, each deployment — with a label, because
a key you can revoke on its own is a key you will revoke. A key is minted in **the world of the
instance the verb knocks at** — its `PINECALL_WORLD`, and the prefix says it (`pc_live_`,
`pc_test_`): the sandbox instance's worker key is the sandbox's, production's is production's.
`--env` may only name that same world; the other one is `400`, with the URL where it answers,
because a key minted here for the other instance would open nothing in either. `--scope`, repeatable, is what the key may do (`app` · `calls` · `talk` · `supervise` ·
`pipeline` · `words` — the org's lexicon — · `knowledge` · `memory` · `evals` · `numbers` ·
`keys` — the org's own API keys — · `providers` — the vendor keys it brought — · `team` · `usage`
· `fleet`); left out is every scope
but `fleet`, the box's own worker's, minted only when typed (`pinecall-worker-key@.service` types
it). An org makes its servers' tokens without the operator, in its console (`POST /v1/keys`); these
verbs are the box's way in, on `PINECALL_OPS_KEY`. `--subject` and `--name` make it a person's key
(`pc_…`), which acts in the world of the instance it knocks at as any person's — `--env` says
nothing then — and **never expires, on either instance**: unlike the day a sandbox's sign-in
lasts, it is the operator's own tool, revoked by hand.

## `routes`

```
pinecall-runtime routes list [--org <org>] [--env production|sandbox]
pinecall-runtime routes add <number> <agent> [--channel phone|whatsapp] [--org <org>] [--env …]
pinecall-runtime routes rm  <number> [--org <org>]
pinecall-runtime routes seed [--file infra/seed/routes.json]
```

Which number reaches which agent, through which door, in which world. A number belongs to one agent
at a time; adding it again moves it — to another agent, or with `--env` to the other world. `list`
answers **one org** (`default` unless `--org` names another, as everywhere here) in one world,
production unless asked: number, channel, agent, and who put the door there — `operator`, typed
with `add`, or `app`, declared by a running `pinecall start` and answered while it holds it. A
`web` door is the browser's: no carrier, no number, a `—`. `seed` applies a file of them (each may
carry an `env`), which brings a box up from a checkout instead of six commands.

## `fleet`

```
pinecall-runtime fleet list
pinecall-runtime fleet cordon <worker>
pinecall-runtime fleet uncordon <worker>
pinecall-runtime fleet loop --cloud <gcp|aws|hetzner|./yours> --seats <n> [--target 0.6] [--min 1] [--max 10] [--every 15] [--once] [--dry-run]
```

`list` is the roster the hub hears — one line per worker that ever knocked, what it holds, its seats
(`cpu` where no `PINECALL_MAX_JOBS` was baked in) and load, its standing (`accepting` · `full` ·
`draining` · `cordoned` · `gone`), when last heard — and the totals: `free = Σ(max − active)`, or
`seats gated by cpu, uncounted` when nobody counted, how many accept, **FULL** when nobody does.

```
worker             held  seats  load  standing   heard
pinecall-box       1     cpu    0.31  accepting  3s ago
pinecall-worker-1  2     4      0.50  accepting  4s ago

2 up · 3 calls · 2 seats free · 2 accepting
```

`cordon` is the graceful shrink: the worker is told on its next heartbeat, takes no new call,
finishes the ones it holds, and leaves. `uncordon` takes it back while it is still there. `loop` is
the fleet loop ([scaling.md](scaling.md)): every `--every` seconds it reads the roster and the cloud
and keeps `busy = active / seats` at `--target` — a machine when over it, a cordon on the quietest
when under it by 0.15 or more, a delete once a cordoned machine holds nothing or never dialled in. `--seats` is the `PINECALL_MAX_JOBS` baked into the image, so
a machine still booting counts from the moment it is asked for. `--cloud` names a script under
`infra/fleet/` or a path to yours ([../infra/fleet/README.md](../infra/fleet/README.md)); the
cloud's own CLI must be signed in wherever the loop runs. `--once --dry-run` prints one tick's
verdict and touches nothing.

## `migrate`

```
pinecall-runtime migrate up [--schema public] [--post]
pinecall-runtime migrate status
pinecall-runtime migrate plan
```

The `.sql` files under `pinecall/migrations`, applied in order, over `DATABASE_URL`. It is what a
unit runs before every start, so it prints no secret: the `default` org is seeded here and its
first key is `keys issue`, never this verb. **`migrate` with no verb READS** — `status`, which
asks the DATABASE what it has run: `applied`, `behind` (a startup file it has not) and `waiting`
(a `.post.sql` nobody has applied yet), counted at the end. `plan` names what a run of that kind
would apply, off the disk. Applying is `migrate up`, typed in full; `--schema` applies into a
schema of its own, how a test run owns its copy. **`--post` is the other half, never run at
startup**: a migration is held to five seconds there, so anything slower is written as a
`.post.sql` and applied by a person, after the deploy, with this flag — an index over a big table
always is.

## `doctor`

```
pinecall-runtime doctor [--mail-to <address>]
```

Every dependency asked a real question — is the key present, does it answer, is the port open — one
line each. It is the first to run on a box behaving strangely, and the last after a deploy.

```console
$ pinecall-runtime doctor
env: /Users/berna/pinecall-v2/runtime/.env

✓ api keys              the api_keys table — `pinecall-runtime keys issue --org <slug>` mints one
✓ provider keys         llm ANTHROPIC_API_KEY, OPENAI_API_KEY · stt SONIOX_API_KEY, … · tts ELEVEN_API_KEY
✓ provider keys answer  ANTHROPIC_API_KEY · OPENAI_API_KEY · SONIOX_API_KEY · …
✓ livekit               http://127.0.0.1:7880/ — HTTP 200
✓ postgres              postgresql://pinecall@[::1]:5432/pinecall — vector, pg_textsearch
! embedder              tei · BAAI/bge-m3 — http://127.0.0.1:8081 — ConnectError: …;
                        a lookup without it is skipped and said in the call's log: this stops no call
! mail                  not configured — set it at PUT /v1/ops/mail (Box settings), or set PINECALL_SMTP_URL and PINECALL_MAIL_FROM, …
! lk                    not installed — brew install livekit-cli (lk docs · lk sip · lk dispatch)

all up
```

`✓` is answered, `!` is advice — something degraded that stops no call — and `✗` is broken; the last
line is `all up` or `first down: <check> — …`, and the exit code is 1 on a `✗`. What it asks after
depends on `PINECALL_ROLE`: `all`, `hub` (no worker) or `worker`, which is not asked after Postgres,
the embedder or the mail. On a `hub` a dead embedder is `✗`, not advice: a hub answers knowledge
pushes. The mail line names the server the box posts invitations and password resets through, as the
gateway resolves it: the mailbox the operator stored at `PUT /v1/ops/mail` first, else
`PINECALL_SMTP_URL` and `PINECALL_MAIL_FROM`. `--mail-to` posts one real test letter through it
after the report — `mail sent  <address> — taken by <host>:<port>`, or the server's own refusal —
and a letter that did not go makes the exit code 1. The report's first line names the .env read and
the instance — `world <production|sandbox> · fleet <name>` — because two instances run from one
checkout on a box and a green report must say whose it is. The api keys line once read differently on a
laptop, which could run on `PINECALL_DEV_KEY` — one key, org `default`, the table not read; that
second runtime is gone, and the line names the verb that puts a key in the one table there is.

## `providers`

```
pinecall-runtime providers [--does llm|stt|tts]
```

Every vendor this build can reach, what each does, and whether it has a key: `ready` · `no key` ·
`no plugin` · `its own`. `←` marks the vendor this build runs that stage on when an agent names
none, and the last line counts the table. The same one `pinecall providers` prints for a tenant.

```console
$ pinecall-runtime providers
vendor        does         standing   variable              also known as
livekit       llm,stt,tts  ready                            inference lk
anthropic ←   llm          ready      ANTHROPIC_API_KEY     claude
…
48 vendors · ours: llm anthropic · stt soniox · tts cartesia
```

A vendor an org brought of its own is `orgs provider-key`, above; this table is the box's.

## `box`

```
pinecall-runtime box secrets [--instance <name>] [--into /etc/credstore.encrypted]
pinecall-runtime box secret <NAME> [--instance <name>] [--into …]   # the value on stdin
pinecall-runtime box instance <name> --world production|sandbox --domain <host> [--port N]
                              [--fleet F] [--identity URL] [--elsewhere URL] [--sandbox URL]
                              [--max-jobs N] [--idle-processes N] [--force]
pinecall-runtime box database                          # as pinecall-db@<name> runs it
pinecall-runtime box peer --from <instance> --into <instance> [--force]
pinecall-runtime box peer --among <instance>…          # as `make converge` runs it
```

`secrets` generates, once, everything a box makes for itself and nobody issues to it: the LiveKit
pair, the Postgres password and `DATABASE_URL`, `PINECALL_OPS_KEY`, `PINECALL_VAULT_KEY`, and the
`media.env` the three containers read. Run twice it **rotates nothing** — what is there is kept and
only what is missing is made. `secret` keeps one secret you bring under its own name, read from
stdin: a provider key, WhatsApp's token. Both write systemd encrypted credentials, which is why the
runtime reads `CREDENTIALS_DIRECTORY` as a source of settings: on a box a secret is a file the unit
decrypts, not a line in an environment file.

**An instance** is one runtime on the box — its gateway, worker, database, fleet and keys — and is
two things: `/etc/pinecall/instances/<name>.env` and `/etc/pinecall/instances/<name>.credstore/`
([infra/box/README.md](../infra/box/README.md), "An instance"). `instance` writes the first, every
variable of it, an unset one as `NAME=` so a line box.env still carries never becomes this
instance's: the world, the fleet (`pinecall` for `production`, else `pinecall-<name>`), the domain,
`PINECALL_GATEWAY_URL` on loopback (8080 for `production`, else the next hundred no other
instance's file holds — 8180, 8280 — checked against every other file), the worker's health port
two above it, `PINECALL_RECORDINGS` under `/var/lib/pinecall/recordings/<name>` (made `2770
pinecall:pinecall-media`), identity, elsewhere, production's `--sandbox` (the URL it asks whose a
ring is), and the worker's two knobs. It refuses a name that
is not a slug of at most 32, a port another instance holds, a sandbox with no `--identity` (it
would never start), and a file that is already there unless `--force`. `secrets --instance <name>`
draws that instance's own three — `DATABASE_URL` on a role and database of its own,
`pinecall_<name>` (a dash becomes `_`), `PINECALL_OPS_KEY`, `PINECALL_VAULT_KEY` — into its store,
never rewriting one; no LiveKit pair and no `media.env`, which are the box's, and no worker key,
which `pinecall-worker-key@<name>` mints. `production` is refused there: its database is the
container's own, so its three are the box's first draw, which `make install` copies into its
store. `secret --instance <name>` puts one you bring into that store instead of the box's.
`database` is what `pinecall-db@<name>` runs as root: if the database its `DATABASE_URL` names is
missing, it makes the role, the database owned by it, takes `CONNECT` on every database from
`PUBLIC` — so each role reaches its own and no other — and creates `vector` and `pg_textsearch` in
it; where the database is there, production's always, it does nothing.

`peer`, as root, gives two instances their trust in each other (infra/box/README.md, "Peers"):
it mints a fleet key at `--from`'s gateway — its `PINECALL_GATEWAY_URL`, its ops key decrypted out
of its store, org `default`, scopes `fleet` and `app`, labelled `peer-for-<into>`, in `--from`'s
world — and encrypts it into `--into`'s store under the name that says what it opens:
`PINECALL_SANDBOX_KEY` when `--from` is a sandbox, `PINECALL_PEER_KEY` when it is production. A key
already kept is refused without `--force` (and one forced over stays live at `--from` until `keys
revoke`). `--among` is every pair among those instances — a production whose `PINECALL_SANDBOX_URL`
host is another one's `PINECALL_DOMAIN`, both ways — minting only what is missing, and saying and
skipping a gateway that does not answer yet.

---

## Where it reads its settings, and how a runtime is brought up

Every variable these verbs read, and the two walkthroughs — a laptop from nothing and a box
from nothing — are [the-environment.md](the-environment.md).
