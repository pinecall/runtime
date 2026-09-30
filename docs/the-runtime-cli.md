# `pinecall-runtime`

The operator's terminal: the two processes, the database, the tenants and the fleet. One verb per
group; `pinecall-runtime --help` prints them all, `<group> --help` that group's verbs. The tenant's
terminal is `pinecall`, the agents repo's `docs/the-cli.md`, and they never overlap: nothing here
writes an agent, nothing there issues a key.

## What each group speaks to

| group | speaks to |
|---|---|
| `gateway` · `worker` · `doctor` · `providers` | this machine: its settings, its database, its LiveKit |
| `migrate` · `sessions` · `memory` · `retention` · `traceback` | Postgres, straight, over `DATABASE_URL` |
| `box up` · `box upgrade` | this machine as root: it made a box from the package itself |
| `init` · `orgs` · `keys` · `routes` · `fleet` | a running gateway, over `/v1/ops/*` with `PINECALL_OPS_KEY` ([protocol/operator-api.md](protocol/operator-api.md)); `keys fleet` alone is minted on the database, before any gateway answers |
| `load` | a running gateway's sandbox, over the worker's own call doors with the sandbox fleet's key (`PINECALL_WORKER_KEY`) |

## `gateway` · `worker start` · `worker overflow`

`gateway` serves both worlds on the loopback address `PINECALL_GATEWAY_URL` names, behind Caddy;
a URL that is not loopback is refused in one sentence. `worker start` is a worker of the fleet
`PINECALL_FLEET` names, until told to stop or cordoned; `worker overflow` the one that answers when
the fleet is full. Every variable they read is [the-environment.md](the-environment.md).

## `box up` · `box upgrade`

`sudo uvx --from pinecall pinecall-runtime box up --domains <production>[,<sandbox>]` makes the
machine it runs on a box, from the files the package carries (`pinecall/infra/`): the system's
packages, `/opt/pinecall/infra`, `install.sh` (containers, firewall, Caddy for the names, the
secrets drawn and sealed), then `release.sh` with `PACKAGE=pinecall==<this version>` — the
runtime from PyPI into `/opt/pinecall/venv`, migrations, the units, the doctor. Ubuntu 24.04 and
root; the names already point at the machine. `--backup-key age1…` writes
`/etc/pinecall/backup.age.pub` and turns the nightly backup on; without it there is none.
`--package` installs a wheel's path or another `pinecall==` instead. `box upgrade` is `box up`
with the names the box has (`/etc/pinecall/box.env`): run from `uvx --from pinecall@latest`, it
brings the box to that version. On a box, `/usr/local/bin/pinecall-runtime` runs any verb with the
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
```

`issue` prints the key once; the table keeps the fingerprint. `fleet` mints a world's fleet key on
the database, printed once where the unit that seals it reads it.

## `routes`

```
routes list [--org] [--env] · routes add <number> <agent> [--channel phone|whatsapp] [--org] [--env]
routes rm <number> [--org] · routes seed [--file infra/seed/routes.json]
```

## `fleet`

```
fleet list [--fleet <name>] · fleet cordon <worker> · fleet uncordon <worker>
fleet loop --cloud <script> --seats <n> [--fleet <name>] [--target 0.6] [--min 1] [--max 10] [--every 15] [--once] [--dry-run]
```

`list` is the roster the gateway hears: each worker, what it holds, its seats, load, standing and
when it was heard, then each fleet summed. `loop` keeps a fleet at its target ([scaling.md](scaling.md)):
`--cloud` is a script with three verbs, `create <name>`, `delete <name>`, `list`; `infra/fleet/`
holds one per cloud. `--once --dry-run` prints one tick's verdict and touches nothing.

## `load`

```
pinecall-runtime load --org <id> --agent <slug> --script <call-log.json> --calls <n> --ramp <seconds> --minutes <m>
```

Synthetic calls held against the gateway at `PINECALL_GATEWAY_URL` exactly as workers hold them,
so the control plane is measured without LiveKit, audio or a vendor. Each call knocks the doors a
worker knocks, through the worker's own client: it opens a call in the **sandbox** world (there is
no other), writes the script's entries a worker writes at the script's own gaps, one request in
flight at a time, then its `call.ended`, and seals it; the entries the gateway writes itself
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
hang-up judging off (`PUT /v1/org/judging`), or every seal asks the box's judge model. One
connection per call is kept open, so `ulimit -n` must be above `--calls`. At the end it prints,
a line each:

| line | what it is |
|---|---|
| `calls opened` · `calls sealed` | calls the gateway opened, and those sealed after their `call.ended` |
| `calls open at the end` | calls still writing past the run's end and a grace of 60 s, cut |
| `most held at once` | the most calls open together |
| `entries sent` | appends the gateway answered, and how many of them it keeps (durable) |
| `entries a second at the top` | appends answered between the end of the ramp and the end of the run, per second |
| `append ms` · `seal ms` | the time one append or one seal took, as the worker waits it, p50/p95/p99 and p50/p99 |
| `refusals` | the calls a refusal ended, by the status the gateway answered (`unreachable` for none) |
| `logs verified` · `logs found wrong` · `logs unread` | each sealed call's log read back: every durable entry sent, once each, in order, and seqs that rose; `unread` counts the reads refused, by status: today every one (403), since a fleet key does not open `calls`, so only the seqs are checked |
| `loop lag ms` | the p99 lag of the generator's own event loop; over 50 ms a `warning:` line follows, since a saturated generator measures itself |

## `sessions` · `memory` · `retention` · `traceback` · `migrate` · `providers` · `doctor`

`sessions list [--agent] [--limit]`, `sessions show <call> [--json]`, `sessions tail [<call>]`,
`sessions recording <call>`: the log read back off Postgres, every tenant's; each read of a call is a row of its org's access log (`reader: operator`), and so is each org a `traceback` showed. `memory reembed`
embeds every fact another model wrote under the box's embedder. `retention due` lists the sealed
calls past their org's `retention_days` (`PUT /v1/org/policy`), oldest first; `retention run`
erases them, each through the erasure path with `retention` as who asked, 5 000 a run at most,
then forgets the detail records of erased phone calls, and the dials, older than 24 months;
`pinecall-retention.timer` runs it at 04:00 every night. `traceback <number> [--since
YYYY-MM-DD]` answers a carrier's traceback: every phone call with the number, still kept or erased
with its record, and every dial to it placed or refused, with the org, the world, the number shown
and who asked — 24 months back unless `--since` says a day. `migrate up` applies what the
database lacks, `migrate status` says what it lacks (exit 1 while behind), `migrate plan` names
every migration on the disk. `providers list [--does llm|stt|tts]` lists every vendor this build
runs and whether the box holds its key; `providers seed <file>` writes the providers row a box
starts from, once: after it, the console edits it at `/v1/ops/providers`. `providers prices
<file.csv> [--apply]` says what a prices file changes in the row's rates (new, changed, the same,
and the models only the box holds, which it keeps) and writes nothing until `--apply`; the box
ships `infra/box/prices.csv`. `doctor` asks each thing the box needs one question, a line
each, and exits 1 when one is missing; it is the last line of every deploy.
