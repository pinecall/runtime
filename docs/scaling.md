# Scaling: one server to a fleet

The same runtime is one process on a laptop, one server that does everything, or a control plane
with workers in as many machines as you need. It is **configuration, not a rewrite** — the machine
you deploy is `PINECALL_ROLE` in a file, and nothing else moves.

This page is what is true in the code today, and says so line by line. The operator's verbs are
[the-runtime-cli.md](the-runtime-cli.md); the box that runs all of this is
[../infra/box/README.md](../infra/box/README.md); the per-cloud scripts are
[../infra/fleet/README.md](../infra/fleet/README.md).

## Three planes, each grows on its own

| plane | what it is | grows with |
|---|---|---|
| **Control plane** | the gateway: call records, keys, routes, quotas, the roster | requests, never calls |
| **Media plane** | LiveKit rooms, SIP trunks, WebRTC | one per region, next to the callers |
| **Workers** | the conversations: speech, the model, the voice | one to thousands, each a number of calls |

One machine runs all three (`PINECALL_ROLE=all`). A **hub** runs the control and media planes and
holds no calls; a **worker** holds nothing but calls and dials the hub by name. That is the whole
of the split, in `infra/box/`.

## Capacity is counted in calls, never in CPU

Each worker reports one number to LiveKit: the share of its seats in use, **live calls over the
calls it was measured to hold** (`worker/load.py`, `SlotLoad`). LiveKit picks the worker for a new
call at random, weighted by the room each has left, and stops routing to one at **0.7** of its seats.

```
a worker's load  = active calls / PINECALL_MAX_JOBS
the fleet's free  = Σ (max − active)   over the workers heard from in the last 30 s
```

`PINECALL_MAX_JOBS` is **measured, never guessed**: on the machine type it will run on, calls with
real audio in a loop, five more each step, until the p95 of first audio crosses 1.8 s. That
concurrency is the ceiling, and `MAX_JOBS` is one under it, because LiveKit re-reads the load every
half second and two calls inside that window both see the old count.

> A machine's CPU measures its neighbours as well as its calls, which is how a shared box stops
> taking calls at the fourth one. **Seats measure only what a worker was built to hold** — so a
> worker unset with `MAX_JOBS` falls back to the CPU average, which is right for a box it shares
> with the SFU and wrong for one it has to itself.

## The hub hears every worker

Every `dev` or `start` worker **heartbeats** to the gateway every five seconds
(`POST /v1/fleet/heartbeat`, with the fleet's own org key): its name — the machine's hostname,
which on every cloud is the instance's name — the calls it holds, its measured seats, its load.
The gateway keeps the **roster** in memory (`fleet/roster.py`); a restart forgets it and the next
round of heartbeats writes it again. `pinecall-runtime fleet list` is that roster as a person reads
it, and `free`, `accepting` and **full** are read off it on every request that needs them.

```
worker             held  seats  load  standing   heard
pinecall-box       1     cpu    0.31  accepting  3s ago
pinecall-worker-1  2     4      0.50  accepting  4s ago

2 up · 3 calls · 2 seats free · 2 accepting
```

## Workers that dial out

A worker opens **one outbound connection** to the hub and asks for calls. No public address, no
open port but ssh, no load balancer to configure (`infra/box/nftables.conf`, `pinecall-worker.service`).
Add a worker and it takes calls within minutes; its media goes to the hub's public UDP port, its
control to `PINECALL_GATEWAY_URL`, and it knocks with an org key minted on the hub.

A worker on a full box (`role=all`) keeps its own health server on **loopback:8082**
(`PINECALL_WORKER_HTTP_PORT`) — never the gateway's 8080 or the embedder's 8081, which share the machine.

## Deploys that drain, not cut

A restart is a drain. On `SIGTERM` the worker tells LiveKit it is full and waits for the calls it
holds to end, for up to **ten minutes** (`DRAIN_S`, `worker/main.py`); past that each remaining job
is shut down and seals its log as `drained`. systemd gives it **fifteen minutes** of grace
(`TimeoutStopSec=900`) where its default 90 s would cut a call mid-sentence. `make deploy` restarts
the gateway first and the worker only once the gateway answers, so no call rings into the gap.

The gateway drains nothing, because it holds nothing a call cannot get back. It stops in seconds
(uvicorn's `timeout_graceful_shutdown` of 5 s, `TimeoutStopSec=30`), and while it is away each
worker asks again — the call's entries, a tool, its command stream — on a backoff; the first door
that answers `404` for a call the worker holds is told the call again (`POST
/v1/calls/{call}/reopened`), and the app's socket, reconnecting, hears `call.attached`. A caller in
the middle of a sentence hears nothing of it: the audio is LiveKit's and the worker's.
[protocol/a-deploy-never-cuts-a-call.md](protocol/a-deploy-never-cuts-a-call.md) is the whole of it.

## Cordon: the graceful shrink

`pinecall-runtime fleet cordon <worker>` is the drain an operator asks for. The worker learns on its
next heartbeat, tells LiveKit it is full, finishes the calls it holds, and exits **3** — the code
`RestartPreventExitStatus=3` in its unit leaves down, because the machine is about to be deleted or
a person will start it back. `fleet uncordon` takes it back **while it is still there**, and says
which it did: a worker still heartbeating takes calls again, and one that already drained is named
as gone with the line that starts it (`systemctl start pinecall-worker` on a box). That matters on
a box of one worker: a cordon there is not a shrink, it is the end of the service until somebody
types that line, and the roster keeps saying `accepting` for the thirty seconds a heartbeat counts
for. It is the same drain, with the same ten minutes.

## Concurrency per client, held at the door

What a plan sells is one number — the calls a tenant may hold at once — and it is enforced
**before a worker is spent**, not after. `Admission.a_call` refuses a call past the org's
`concurrent_calls` quota, on the control plane, the same way on one server or a thousand
(`orgs/admission.py`). A tenant over the line is refused with a sentence and `credits.exhausted` in
its own log; nothing already inside the call is cut.

## Overflow at the door

When **no worker can take a call** — the roster knows at least one and none accepts — the runtime
says so instead of opening a room nobody joins:

- **The web widget** asks `POST /v1/tokens` for a seat and gets `503` with the numbers and the way
  out, and `fleet.full` lands in the agent's log. The page offers a **call back** before any room is
  made: `POST /v1/callbacks {agent, number}` writes `callback.requested` onto the agent's log, and
  `GET /v1/callbacks` is every request the org's agents took, for its app to dial.
- **A phone call** is answered by the **overflow agent** (`pinecall-runtime worker overflow`, on
  the hub, never a seat). It registers under the fleet's name and reports itself *full* to LiveKit
  until the gateway says every real worker is — LiveKit picks workers at random weighted by room,
  so a worker that merely sat near the line would take a third of a half-full fleet's calls — and
  then it is the one worker the dispatch can still reach. It says one sentence
  (`PINECALL_OVERFLOW_SAYS`), writes the caller's number as `callback.requested`, `via: overflow`,
  and hangs up. No STT, no model: it never fills.

The runtime **records** the call back; placing it is the tenant's app, which has the number, the
agent and the outbound trunk of its own.

## The fleet loop

`pinecall-runtime fleet loop --cloud gcp --seats 4` keeps the fleet at a target — **60 % busy** by
default, `busy = active / seats` over the workers heard from — and it holds nothing between two
ticks: the roster is the hub's and the machines are the cloud's.

Every fifteen seconds (`fleet/decisions.py`, pure numbers in, decisions out):

| when | it does |
|---|---|
| a cordoned machine holds no call | **delete** it |
| a machine never dialled in within 10 min, or fell silent for 5 | **delete** it |
| fewer workers than `--min`, or no seat anywhere, or busy over the target | **grow**: `create pinecall-worker-<n>` |
| busy would still be under the target **by 0.15** without the quietest one, and more than `--min` | **cordon** the quietest |

A machine still booting counts as `--seats` of capacity from the moment it is asked for, so the
loop asks once and waits, and the 0.15 of slack is what keeps a grow and a cordon from chasing
each other across two ticks. The loop never cordons or deletes a machine the cloud does not
**list** as the fleet's: a worker you stood up by hand counts in the numbers and is never let go.

The cloud is one script with three verbs — `create <name>`, `delete <name>`, `list` — and
`infra/fleet/` ships `gcp`, `aws` and `hetzner` at about forty lines each; a cloud of your own is
`--cloud ./yours`. **The image is a worker that was deployed once and frozen**: a machine made from
it boots with the code, the units, the credentials and `box.env`, its hostname is the name the loop
gave it, and it dials the hub by itself. Nothing is copied at boot. The loop runs wherever the
cloud's CLI is signed in — a laptop, or the hub as `pinecall-fleet.service` once `box.env` names
`PINECALL_FLEET_CLOUD`.

## Measured, 2026-09-11

On the real hub (`pinecall-box`, GCP `e2-standard-4`) with one worker by hand and the loop run
from a laptop: `fleet loop --min 2` asked GCP for `pinecall-worker-2` from the image and the new
machine **heartbeated 47 s after `create` returned**; `--min 1` then cordoned it (the worker
exited 3 five seconds later, `NRestarts=0`), deleted the machine on the next tick, and held on the
one after. With both workers cordoned, `POST /v1/tokens` answered the `503` above, `POST
/v1/callbacks` took a number, and a dispatch into a room with a visitor in it was answered by the
overflow agent: the visitor heard the agent's track, and the call's log closed with `call.ended`,
`agent_hung_up`, **10.7 s** after it opened. Every number here came out of that afternoon's
terminal, not a benchmark.

## The shape of the numbers

The landing's worked example, for reference — the arithmetic, not a benchmark:

```
50,000 calls a day · 4 min a call · 10 busy hours · ×2 peak-over-average
  = 50000 × 4 ÷ 60 ÷ 10 × 2  ≈  667 calls at once at the peak
```

At ~16 live calls a worker and 60 % target busy, that is ~70 servers kept ready — which is
`--max 70` and the loop's job.

## What is not in the code

One line of the landing page is still ahead of the runtime: **media in every region**. LiveKit's
open-source server runs one region; routing a caller to the SFU nearest them is a cloud feature or
a second full stack behind a geographic DNS answer, and this repository ships neither yet. Every
other line above is code you can read, with a test beside it.
