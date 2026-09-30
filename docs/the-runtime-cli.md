# `pinecall-runtime`

The operator's terminal: the two processes, the database, the tenants and the fleet. One verb per
group; `pinecall-runtime --help` prints them all, `<group> --help` that group's verbs. The tenant's
terminal is `pinecall`, the agents repo's `docs/the-cli.md`, and they never overlap: nothing here
writes an agent, nothing there issues a key.

## What each group speaks to

| group | speaks to |
|---|---|
| `gateway` · `worker` · `runner` · `doctor` · `providers` | this machine: its settings, its database, its LiveKit |
| `migrate` · `sessions` · `memory` · `retention` · `traceback` · `facts` · `vault` | Postgres, straight, over `DATABASE_URL` (`vault` with `PINECALL_VAULT_KEY` too) |
| `box up` · `box upgrade` | this machine as root: it made a box from the package itself |
| `init` · `orgs` · `keys` · `routes` · `fleet` | a running gateway, over `/v1/ops/*` with `PINECALL_OPS_KEY` ([protocol/operator-api.md](protocol/operator-api.md)); `keys fleet` and `keys runner` alone are minted on the database, before any gateway answers |
| `load` | a running gateway's sandbox, over the worker's own call doors with the sandbox fleet's key (`PINECALL_WORKER_KEY`) |

## `gateway` · `worker start` · `worker overflow` · `runner start`

`gateway` serves both worlds on the loopback address `PINECALL_GATEWAY_URL` names, behind Caddy;
a URL that is not loopback is refused in one sentence. `worker start` is a worker of the fleet
`PINECALL_FLEET` names, until told to stop or cordoned; `worker overflow` the one that answers when
the fleet is full. `runner start` keeps the hosted apps of the world its `PINECALL_RUNNER_KEY`
opens running, one gVisor container each, on a machine that is not the box
([../infra/apps/README.md](../infra/apps/README.md)). Every variable they read is
[the-environment.md](the-environment.md).

## `box up` · `box upgrade` · `box failover`

`sudo uvx --from pinecall pinecall-runtime box up --domains <production>[,<sandbox>]` makes the
machine it runs on a box, from the files the package carries (`pinecall/infra/`): the system's
packages, `/opt/pinecall/infra`, `install.sh` (containers, firewall, Caddy for the names, the
secrets drawn and sealed), then `release.sh` with `PACKAGE=pinecall==<this version>` — the
runtime from PyPI into `/opt/pinecall/venv`, migrations, the units, the doctor. Ubuntu 24.04 and
root; the names already point at the machine. `--backup-key age1…` writes
`/etc/pinecall/backup.age.pub` and turns the nightly backup on; without it there is none.
`--package` installs a wheel's path or another `pinecall==` instead. `box upgrade` is `box up`
with the names the box has (`/etc/pinecall/box.env`): run from `uvx --from pinecall@latest`, it
brings the box to that version. `box failover`, on the machine `infra/cell/replica.sh` made a replica, promotes its
Postgres and prints what to repoint; it repoints nothing itself ([a-box-in-production.md](a-box-in-production.md)). On a box, `/usr/local/bin/pinecall-runtime` runs any verb with the
box's settings and sealed credentials: `sudo pinecall-runtime doctor`, `sudo pinecall-runtime init …`.

## `init`

```
pinecall-runtime init [--org <slug>] --email <address> --person "<name>" [--name "…"] [--role admin]
```

The first org and the first person on a runtime nobody has used yet: the org made (or found, run
twice), its first admin invited, that person made an operator of this box, and the invitation link
printed once with the two lines to type next.

## `orgs`

```
orgs list · orgs add <slug> [--name] · orgs rm <org>
orgs invite <org> <email> --name "…" [--role] · orgs operator <org> <email> [--revoke] · orgs remove-member <org> <email>
orgs move <agent> <org>
orgs quota <org> --env production|sandbox [--minutes n] [--messages n] [--agents n] [--concurrent-calls n]
           [--memory-facts n] [--knowledge-chunks n] [--numbers n] [--seats n] [--llm-tokens n]
           [--budget-usd n] [--lends vendor[/model],… | --lends none]
orgs dialling <org> [--dial-anywhere | --no-dial-anywhere] [--per-minute n] [--per-day n] [--max-duration-s n]
orgs sso <org> [--off]
orgs provider-key set|rm|list <org> <vendor>        # set reads the key from stdin
```

`<org>` is an id or a slug. `quota` replaces the whole set **for one world**; `dialling` the whole
set of guards; `sso --off` is the break-glass; `provider-key set` reads from stdin because argv is
what `ps` shows.

## `keys`

```
keys issue [--org <org>] --env production|sandbox [--label "…"] [--scope <scope>]… [--subject <member>] [--name "…"]
keys list [--org <org>] · keys revoke <fingerprint> · keys fleet production|sandbox
keys runner production|sandbox
```

`issue` prints the key once; the table keeps the fingerprint. `fleet` mints a world's fleet key on
the database, printed once where the unit that seals it reads it; `runner` mints the key of the
world's runner the same way ([protocol/hosting.md](protocol/hosting.md)).

## `routes`

```
routes list [--org] [--env] · routes add <number> <agent> [--channel phone|whatsapp] [--org] [--env]
routes rm <number> [--org] · routes seed [--file infra/seed/routes.json]
```

## `fleet`

```
fleet list [--fleet <name>] · fleet cordon <worker> · fleet uncordon <worker>
fleet loop --cloud <script> --seats <n> [--fleet <name>] [--target 0.6] [--min 1] [--max 10]
           [--grow-at-most 1] [--every 15] [--once] [--dry-run]
```

`list` is the roster the gateway hears: each worker, what it holds, its seats, load, standing
(`accepting`, `failing`, `full`, `draining`, `cordoned`, `gone`) and when it was heard, then each
fleet summed. `loop` keeps a fleet at its target ([scaling.md](scaling.md)):
`--cloud` is a script with three verbs, `create <name>`, `delete <name>`, `list`; `infra/fleet/`
holds one per cloud. A tick grows by the seats missing, at most `--grow-at-most` machines (1 unless
said), and shrinks by one cordon. `--once --dry-run` prints one tick's verdict and touches nothing.

## `load`

```
pinecall-runtime load --org <id> --agent <slug> --script <call-log.json> --calls <n> --ramp <seconds> --minutes <m>
```

Synthetic calls held against the gateway at `PINECALL_GATEWAY_URL` exactly as workers hold them,
so the control plane is measured without LiveKit, audio or a vendor. Each call knocks the doors a
worker knocks, through the worker's own client: it opens a call in the **sandbox** world (there is
no other), queues the script's entries a worker writes at the script's own gaps on the worker's
own writer, which sends what is queued as one batch while the last is out, then its `call.ended`,
and seals it once all were answered; the entries the gateway writes itself
(`call.ringing`, `call.attached`, `tool.call`, `tool.result`, `call.summary`, `call.score`, memory
and sources) are left out, and so are tool round-trips, which need an app socket. A call that
ends before the run does is followed by a fresh one, so the number held stays at the top; a
refusal ends that call only, and its slot opens another a second later. `--script` is a call's
log as JSON, the shape of `tests/wire/golden/call-log.json` (125 entries over 53.5 s); `--calls`
is how many at once at the top, reached linearly over `--ramp` seconds and held `--minutes`.

```
PINECALL_GATEWAY_URL=https://sandbox.<throwaway box> PINECALL_WORKER_KEY=<its sandbox fleet key> \
  pinecall-runtime load --org org_load --agent load --script tests/wire/golden/call-log.json \
  --calls 500 --ramp 60 --minutes 5
```

It runs against **a box made for it** (`box up` on a clean machine), never production's: the
calls are real calls of the org, logged, counted against its quotas and sealed. The org is one
made for the run, with its sandbox quotas (`orgs quota --env sandbox`) above the run and its
hang-up judging off (`PUT /v1/org/judging`), or every seal asks the box's judge model. Each call
at once has a client and a connection of its own, as a worker's job has, so `ulimit -n` must be
above `--calls`. At the end it prints,
a line each:

| line | what it is |
|---|---|
| `calls opened` · `calls sealed` | calls the gateway opened, and those sealed after their `call.ended` |
| `calls open at the end` | calls still writing past the run's end and a grace of 60 s, cut |
| `most held at once` | the most calls open together |
| `entries sent` | appends the gateway answered, and how many of them it keeps (durable) |
| `entries a second at the top` | appends answered between the end of the ramp and the end of the run, per second |
| `append ms` · `seal ms` | from an entry queued to the log holding it, what the call waits, p50/p95/p99; and the time one seal took, p50/p99 |
| `entries per batch` | how many entries each batch carried, p50/p99: 1 while calls are idle, more as the gateway slows |
| `refusals` | the calls a refusal ended, by the status the gateway answered (`unreachable` for none) |
| `logs verified` · `logs found wrong` · `logs unread` | each sealed call's log read back: every durable entry sent, once each, in order, and seqs that rose; `unread` counts the reads refused, by status: today every one (403), since a fleet key does not open `calls`, so only the seqs are checked |
| `loop lag ms` | the p99 lag of the generator's own event loop; over 50 ms a `warning:` line follows, since a saturated generator measures itself |

## `sessions` · `memory` · `retention` · `traceback` · `facts` · `migrate` · `providers` · `doctor`

`sessions list [--agent] [--limit]`, `sessions show <call> [--json]`, `sessions tail [<call>]`,
`sessions recording <call>`: the log read back off Postgres, every tenant's; each read of a call is a row of its org's access log (`reader: operator`), and so is each org a `traceback` showed. `memory reembed`
embeds every fact another model wrote under the box's embedder. `retention due` lists the sealed
calls past their org's `retention_days` (`PUT /v1/org/policy`), oldest first; `retention run`
erases them, each through the erasure path with `retention` as who asked, 5 000 a run at most,
then forgets the detail records of erased phone calls, and the dials, older than 24 months, and
the WhatsApp message ids claimed more than 7 days ago ([whatsapp.md](protocol/whatsapp.md));
then makes the log's days a week ahead (`call_log` is partitioned by UTC day; a row no day holds lands in `call_log_default`, and a day the default already holds rows of is named and not made) and drops each past day nothing is left in; `pinecall-retention.timer` runs it at 04:00 every night. `doctor`'s `days` line names rows in the default and fewer than two days made ahead. `traceback <number> [--since
YYYY-MM-DD]` answers a carrier's traceback: every phone call with the number, still kept or erased
with its record, and every dial to it placed or refused, with the org, the world, the number shown
and who asked — 24 months back unless `--since` says a day. `migrate up` applies what the
database lacks, `migrate status` says what it lacks (exit 1 while behind), `migrate plan` names
every migration on the disk. `providers list [--does llm|stt|tts]` lists every vendor this build
runs and whether the box holds its key; `providers seed <file>` writes the providers row a box
starts from, once: after it, the console edits it at `/v1/ops/providers`. `providers prices
<file.csv> [--apply]` says what a prices file changes in the row's rates (new, changed, the same,
and the models only the box holds, which it keeps) and writes nothing until `--apply`; the box
ships `infra/box/prices.csv`. `facts rebuild [--call <call>] [--org <org id>] [--since
YYYY-MM-DD]` folds each call's facts row (what the lists, the inbox and the insights read) again
from its log, every call's when no flag is given, and writes the row where it differs: one call at
a time, each in a transaction of its own under the lock its appends take, so a live call waits
milliseconds and nothing holds a long transaction; it prints how many calls it read and how many
rows it rewrote. `usage rebuild` folds every org's
usage totals (what admission counts, a row per org, world and month) again from the summaries in
the log, as the usage feed folds each one, and rewrites the table in one transaction that holds
it: a summary written meanwhile waits and is counted after; it prints how many summaries and rows.
`doctor` asks each thing the box needs one question, a line each, and exits 1
when one is missing; it is the last line of every deploy. Its `facts` line names every log whose
head gave out fewer seqs than its rows hold, and refolds 20 sealed calls from a random point of
the call ids, naming each whose stored facts differ and the columns that do: `facts rebuild
--call` mends one. `drift rebuild [--org <org id>] [--since YYYY-MM-DD]` forgets the drift of
the days the flags name (every org's and every day's when none), each stage's histogram and each
judge's count that `/v1/insights` and `/v1/insights/drift` read, and counts every sealed call of
them again from its log, each in a transaction of its own; it prints how many sealed calls it read
and how many it counted. A seal whose count broke says so in the gateway's journal, and this is what
mends it.

## `vault rotate`

Every secret the box keeps is sealed under the first key of `PINECALL_VAULT_KEY`, and any key the
list holds opens it. To turn the vault: put a new key in front of the list (on a box,
`install.sh secret PINECALL_VAULT_KEY` with `<new>,<old>` on stdin) and restart the gateway, so
every secret written from then on is sealed under the new one; then `vault rotate` re-seals every
sealed value of the schema under the first key, one line per column:

```
box_settings.ciphertext: 3 re-sealed, 0 under the first key already, 0 opened by no key listed
```

The columns are the vendors' keys (the org's and the box's), the carriers, the mailboxes, the
identity providers, the hosted apps' tokens, the org's secrets, the private values of calls' logs
and the recordings' keys. Each row is its own write,
guarded by the value it still holds, so a secret the gateway rewrites meanwhile is left to it; a
run cut short is run again, and finds what it did under the first key. It exits 1 while a value
opens under no key listed: that value was sealed under a key the list no longer holds, and the
old key comes out of the list only once every line says 0 there.
