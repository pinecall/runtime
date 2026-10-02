# infra — what runs where, and how it grows

One package, `pinecall`, is every process: the gateway, a worker, the overflow, the runner, the
operator's CLI. What changes from one box to a fleet of thousands of machines is only **how many
of each run, and where**. This page is the map; each piece has its own page for the details.

## A call, and who talks to whom

```
  phone · browser · WhatsApp
          │  audio (SIP, WebRTC)
          ▼
 ┌──────────────────┐  audio   ┌────────────────────────────────────────┐
 │ LiveKit + SIP    │◄────────►│ WORKER — one process per call          │
 │ the room         │          │ hears (STT) · thinks (LLM) · speaks (TTS)│
 └──────────────────┘          │ writes every turn to the call's log ──┐ │
         ▲ "take room X"       └───────────────────────────────────────┼─┘
         │                                                             │ HTTP
 ┌───────┴─────────────────────────────────────────────────────────────▼──┐
 │ GATEWAY — the API: opens the call, keeps its log, hands tools to the   │
 │ org's app over its socket, serves the console and the SDKs, seals it   │
 │        │                      │                                        │
 │        ▼                      ▼                                        │
 │   POSTGRES (the truth)    REDIS (the live signal; LiveKit's too)       │
 └────────────────────────────────────────────────────────────────────────┘
```

Audio never crosses the gateway: a worker is heavy (about half a core per call), the gateway is
light per call. The log is written before anything is published, and every reader resumes from
it by `seq`: that one rule is what lets everything below be several instead of one.

## Today: one box

```
 ┌─ one VM ─────────────────────────────────────────────────────────────┐
 │  Caddy ─► gateway ─► Postgres · Redis                                │
 │  LiveKit · SIP · egress                                              │
 │  worker@production · worker@sandbox · overflow@production            │
 │  apps/  the orgs' hosted apps run on a machine of their own          │
 └──────────────────────────────────────────────────────────────────────┘
```

`pinecall-runtime box up` makes it from the package on an empty machine ([box/README.md](box/README.md),
[../docs/a-box-in-production.md](../docs/a-box-in-production.md)). Everything a call needs is there.

## Growing: the same box, workers on many machines

```
 ┌─ the box ────────────────┐         ┌─ worker machines ─────────────────┐
 │ gateway · Postgres       │◄──HTTP──│ worker 1 … worker W                │
 │ Redis · LiveKit · SIP    │◄──WS────│ each holds its world's fleet key,  │
 │ fleet loop               │──create─►│ registers, heartbeats, takes calls │
 └──────────────────────────┘ delete  └───────────────────────────────────┘
```

A worker keeps no state: it asks the gateway for everything and can be anywhere that reaches it.
Capacity is counted in **calls**, one seat each, never in CPU. The **fleet loop** keeps each
world's fleet at its target by creating and cordoning machines
([../docs/scaling.md](../docs/scaling.md)); the cloud is one script with three verbs
([fleet/README.md](fleet/README.md): `gcp`, `aws`, `hetzner`, or yours). Built and in use.

## At scale: a cell

```
              ┌─────────────────────────────────┐
              │ balancer — any gateway, any request│
              └────┬──────────┬──────────┬────────┘
             ┌─────▼───┐ ┌────▼────┐ ┌───▼─────┐
             │gateway 1│ │gateway 2│ │gateway N│   no state of their own
             └────┬────┘ └────┬────┘ └────┬────┘
                  └───────────┼───────────┘
          ┌───────────────────┼─────────────────────┐
          ▼                   ▼                     ▼
   ┌────────────┐      ┌────────────┐        ┌──────────────┐
   │ POSTGRES   │      │ REDIS      │        │ the orgs'    │
   │ primary +  │      │ who holds  │        │ apps (sockets)│
   │ replica,   │      │ what, what │        └──────────────┘
   │ WAL archive│      │ just moved │
   └────────────┘      └────────────┘
   ┌─ media ────────────────┐   ┌─ workers ──────────────────┐
   │ LiveKit 1 … M          │◄─►│ machine 1 … W               │
   │ SIP 1 … K              │   │ grown by the fleet loop     │
   └────────────────────────┘   └────────────────────────────┘
```

A **cell** is a whole Pinecall in one region: the gateways, one Postgres, one Redis, the media
nodes and the fleet. An org lives in exactly one cell, so nothing crosses cells during a call, a
cell that fails takes out its share and no more, and an org that must keep its data in Europe
lives in a European cell. Past what one cell holds, the platform grows by cells, with a small
directory saying which org lives where.

What has to be true for gateways to be several: nothing a request needs may live in one
process's memory. The truth is in Postgres already; what is live (which gateway holds an agent's
socket, which call just wrote an entry, an org's calls at once) goes to Redis as a signal that may
be lost, because a reader that misses it reads the log. **Next**: this is being built now; today
the gateway is one process.

## How each plane grows

| plane | unit | grows when | shrinks by | today |
|---|---|---|---|---|
| workers | a machine of N seats | seats busy over the target (60 %) | a cordon: takes no call, finishes its own, exits | built, the fleet loop |
| gateways | a process, then a machine | requests a second per process; append p99 | the balancer stops sending; it drains its streams | next: N stateless gateways, then the loop moves them too |
| media | a LiveKit node, a SIP node | rooms per node, packet loss; channels per trunk | a node's limit; the carrier's routing | next, per cell |
| Postgres | one primary | never sideways: a bigger machine, or a new cell | — | primary + replica + restore to any minute |
| Redis | one | never: it is a signal, not a store | — | one, LiveKit's |
| cells | a whole Pinecall | a cell's measured ceiling is in sight | — | designed, not built |

One loop drives the three planes that grow: it reads what the gateway knows (`/v1/ops/fleets`,
`/metrics`), decides in one pure function, and calls the cloud's script. Scaling in never cuts a
call: a worker is cordoned and leaves when empty; a gateway is taken off the balancer and drains;
a media node stops taking rooms.

## On any cloud

The runtime does not know where it runs: a machine with Ubuntu, podman and the package. What
each cloud provides:

| piece | GCP | AWS | your own machines |
|---|---|---|---|
| machines for the fleet | a managed instance group (`terraform/modules/fleet-gcp`, `fleet/gcp-mig.py`), or `fleet/gcp` | an Auto Scaling group (`terraform/modules/fleet-aws`, `fleet/aws-asg.py`) | `fleet/hetzner`, or a script of yours |
| balancer in front of the gateways | a cloud LB, or Caddy | idem | Caddy, HAProxy |
| Postgres | a VM (this tree), or managed | idem | a VM, its replica in `cell/` |
| Redis | a VM (this tree), or managed | idem | a VM |
| object store: backups, WAL, recordings | any S3 endpoint (GCS through its S3 keys) | S3 | MinIO, or the disk |
| media | VMs with public addresses: audio is UDP and skips the balancer | idem | idem |

Nothing in this tree names a cloud but the fleet scripts. The object store is spoken in S3 to
whatever answers it; unset, the box keeps everything on its disk.

## When something dies

| dies | the caller | how it comes back |
|---|---|---|
| a worker | one sentence and a call back offered | LiveKit's webhook tells the gateway; the call is sealed as drained |
| a gateway (with several) | nothing | another takes the next request; readers resume from the log |
| Redis | the call goes on; live readers catch up from the log | it reconnects |
| Postgres | the cell stops until the replica is promoted | `box failover`, restore to any minute from the WAL archive |
| a LiveKit node | its calls hear the sentence; new ones land elsewhere | the node's rooms are gone with it |
| a whole cell | its orgs, nobody else | a cell is one region on purpose: no active-active |

## The folders

```
apps/       the machine that runs the orgs' hosted apps, one gVisor container each: apps/README.md
box/        the box: cloud-init, install.sh, release.sh, the systemd units, the containers of the
            media plane, Caddy, nftables: box/README.md
cell/       the second machine that holds a streaming replica of the box's Postgres: cell/README.md
fleet/      one executable per cloud, three verbs (create, delete, list): fleet/README.md
models/     the open stack: three model servers on one GPU, and the row that points a box at them
postgres/   the image of the box's Postgres 17 with pgvector and pg_textsearch
```

Nothing runs on a laptop but the suites' Postgres. A call is tried against a box, and
the box's secrets are drawn on it and sealed there, never in this tree.
