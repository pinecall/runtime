# Scaling: one box to a fleet per world

The same runtime is one box that does everything, or a gateway with workers on as many machines as
the calls need. It is configuration: a worker is `pinecall-runtime worker start` on a machine that
reaches the gateway and LiveKit, holding its world's fleet key. The operator's verbs are
[the-runtime-cli.md](the-runtime-cli.md); the box is [../infra/box/README.md](../infra/box/README.md);
the clouds a fleet grows on are [../infra/fleet/README.md](../infra/fleet/README.md).

## Growing it, step by step

Each step is a command on a machine, and each was measured on 2026-10-01 (the numbers are the
table at the end). Stop at the first one that holds your peak.

| you need | do | holds (measured) |
|---|---|---|
| a box | `sudo uvx --from pinecall pinecall-runtime box up --domains <prod>,<sandbox>` on Ubuntu 24.04 | two gateways, Postgres, LiveKit, two workers per world |
| more calls at once | workers on more machines: the fleet loop asks your cloud for them ([../infra/fleet/README.md](../infra/fleet/README.md)) | ~16 calls a 4-vCPU worker machine; workers are 93 % of the cores a call costs |
| more gateway processes | on a bigger box: `systemctl enable --now pinecall-gateway@8880` (not 8082–8085) and its address in Caddy's `(gateways)` | ~330 calls a gateway core |
| gateways off the box | on the box `primary.sh allow-gateway <machine>`; then `primary.sh gateway-credentials \| ssh <machine> gateway.sh join <box> <wheel>` ([a-box-in-production.md](a-box-in-production.md), "Gateways on other machines") | a gateway machine killed under 1 200 calls lost none |
| more media | a second LiveKit node on the box's Redis (same page, "A second LiveKit node") | ~90 voice calls a core, 0 packets lost at 400 |
| Postgres that outlives the box | `primary.sh allow <replica>` and `replica.sh join` ([../infra/cell/README.md](../infra/cell/README.md)) | drilled: promoted in 17 s with no write lost, a box again in under 3 min; a restore to any minute in 79 s |
| past ~15 000–20 000 calls | a second cell: another box and its machines, an org living in one | Postgres grows ~1.4 cores per 1 000 calls; one database is one cell |

## Three planes, each grows on its own

| plane | what it is | grows with |
|---|---|---|
| control | the gateways: logs, keys, routes, quotas, the roster; any serves any door of any call, telling the others what it did on Redis | requests, never calls: another gateway behind the balancer |
| media | LiveKit rooms, SIP, WebRTC | one per region, beside the callers |
| workers | the conversations: the ears, the model, the voice | calls |

The control plane is as many gateways as the requests need. Each keeps in memory only a cache of
what Postgres holds and what is bound to a connection it holds (an app socket, a stream, a written
call's session, a WhatsApp thread) and, for five seconds, a key it verified; everything else is in
Postgres, or said on the signal each second (who holds which agent, the roster, an org's calls at
once). A key revoked, or a person changed (role, agents, production, operator, disabled), at a door is
forgotten on every gateway at once, said on the signal; a change made at a shell (`keys revoke`)
holds for those seconds on a gateway that remembered the key. A call's requests go to one
gateway while it lives, by the `Pinecall-Call` header the balancer hashes, so a second gateway
costs a call nothing; any other gateway answers them the same when that one is gone. The box runs
two ([a-box-in-production.md](a-box-in-production.md), "Two gateways").

## A fleet per world

One gateway serves both worlds; what keeps a test call off a production process is **a fleet of
workers per world**. LiveKit dispatches by agent name, a worker registers under its unit's
`PINECALL_FLEET` (`pinecall` for production, `pinecall-sandbox` for the sandbox by default, the
`fleets` row of the box, `/v1/ops/fleets`), and the gateway dispatches every call to the fleet of
its world. A worker holds its world's fleet key, and the gateway refuses a call of the other world.
Everything below is per fleet: the roster is keyed by fleet, the totals are a fleet's, the loop
takes `--fleet`.

## Capacity is counted in calls

A worker reports one number to LiveKit: live calls over the calls it was measured to hold,
`PINECALL_MAX_JOBS`; unset, its machine's CPU. LiveKit stops routing to one at **0.7**. The
measure is taken, never guessed: on the machine type it will run on, calls with real audio, more
each step, until first audio's p95 crosses what a caller tolerates; `MAX_JOBS` is one under it,
because LiveKit re-reads the load every half second.

**Set it; never leave a worker on CPU.** Measured on 2026-10-01 with spoken calls (Deepgram,
Haiku, Cartesia) against a worker alone on an 8-vCPU machine: on CPU, ten calls placed a second
apart got five agents at 20 % of the machine — each new call's process loads the turn models, the
CPU reading crosses 0.7 for an instant, LiveKit marks the worker unavailable and never offers that
room again; counting (`PINECALL_MAX_JOBS=20`), 14 of 15 calls got their agent (the fifteenth was
the 0.7 line) at 42 % of the machine. **A call costs ~0.24 vCPU**, so a worker machine holds about
`2.9 × vCPU` calls at the line: set `PINECALL_MAX_JOBS` to `vCPU × 4` and the 0.7 line falls there.
That is a machine of workers alone. On the box everything shares the cores, so `install.sh` gives
its four workers 1.5 seats per vCPU between them, two thirds of them production's (a box of
4 vCPU: two and two in production, one and one in the sandbox), from the box measured whole:

**A box, measured whole.** On 2026-10-01, on a box of production's machine type (4 vCPU:
Postgres, LiveKit, SIP, the room's recorder of the time (egress), two gateways and one worker on
it), SIP callers speaking a turn every 10 s and the three vendors faked on their own wire on
another machine (`infra/lab/`): a call costs **~0.5 cores of the box** — ~0.3 the worker, ~0.12
its recording, ~0.1 SIP and LiveKit.

| calls at once | box cores | turns the agent answered | first audio p50 / p95 | ring to live p50 |
|---|---|---|---|---|
| 2 | 1.6 | 24 of 24 | 1.24 / 1.32 s | 8 s |
| 4 | 2.5 | 45 of 45 | 1.26 / 1.35 s | 16 s |
| 6 | 3.5 | 66 of 66 | 1.35 / 1.63 s | 19 s |
| 8 | 4.0 | 70 of 89 | 1.98 / 3.85 s | 49 s |
| 10 | 4.0 | 53 of 74; 3 calls never started | 1.88 / 4.37 s | — |

Such a box holds six calls. Ring to live was a number of its own: each call's process imported
every installed vendor plugin before its pipeline was live (3–5 s on an idle box, and slower with
the box's load); the worker now imports them in its own process, so LiveKit's forkserver preloads
them once and every call's process inherits them (measured the next day: 0.1 s, at any load). A
worker killed under that load: its calls end drained 21 s later and the other worker tells each
caller, as on an idle box. A second `livekit-sip` on another machine, on the box's Redis and
LiveKit, took half of six calls at once, answered as through one.

**The recording moved into the worker (2026-10-02), and the box measured again.** The call's own
session records now, and the room's recorder is gone; on a box alone that moves the cost rather
than removing it: at six calls the worker went from 1.78 to 2.31 cores and the recorder from 0.60
to 0, the box at 3.4 either way (libopus in the call's process costs what it cost in egress's,
on these cores). With every call live from its first second, the box held six calls with every
turn answered (66 of 67, first audio p95 3.2 s) and gave at eight (44 of 84). What it buys is the
next paragraph: the recording goes with the worker, wherever the worker runs.

**A machine of workers alone** (`worker.sh join`, "Workers on other machines" in
[a-box-in-production.md](a-box-in-production.md)), the same callers and fakes, the box keeping
the media plane and the gateways, the recording in each call's process and the file in the bucket:

| calls at once | worker machine (8 vCPU) | per call | the box (4 vCPU), no worker on it | turns answered | first audio p50 / p95 | ring to live |
|---|---|---|---|---|---|---|
| 8 | 1.5 cores | 0.19 | 1.5 cores | 96 of 96 | 1.23 / 1.30 s | 0.1 s |
| 16 | 2.8 cores | 0.17 | 2.3 cores | 185 of 208 | 1.23 / 1.30 s | 0.1 s |
| 24 | 3.5 cores | 0.15 | 1.8 cores | 274 of 274 | 1.23 / 1.30 s | 0.1 s |

**A call costs ~0.15–0.19 vCPU of a worker machine, its recording included**, so `vCPU × 4` seats
(32 on 8 vCPU) is ~60 % of the machine, under the 0.7 line; and the box, holding SIP, LiveKit,
the gateways and Postgres for those calls, spends ~0.1 a call: a 4-vCPU box carries the media of
about 25 calls before it needs a LiveKit node or a SIP node of its own (above). First audio did
not move from 8 to 24: the worker machine was never the bottleneck of these calls, the box was
when the workers shared it.

## The gateway hears every worker

Every five seconds a worker posts its heartbeat, `{fleet, worker, active, max_jobs, load,
draining}` and its last minute, `{ended, failed, errors, turns, first_audio_p95_s}`, and the
answer says whether it is cordoned and whether its fleet is full. A worker silent 30 s is no longer
capacity; one silent an hour is forgotten. The roster is in memory, on every gateway: each keeps the
heartbeats that reached it and says them to the others once a heartbeat, so a worker is counted on
every gateway whichever it beats on, and a cordon set on one stands on all (the newest setting of a
worker's cordon wins). A gateway that restarts has it back after one round of heartbeats; a
heartbeat's `full` is answered from totals at most a second old. `GET /v1/ops/fleet` and `fleet list` read it.

## A worker that is up but bad

A worker's calls run in livekit's job processes, and each tells its worker's main process, over a
datagram socket the worker binds (`fleet/measures.py`), what its batches say: a turn's first
audio (`e2e_latency`), an `error`, and how the call ended. The heartbeat carries the last minute of
it: the calls that ended and those that ended in an error, the error entries, the turns measured
and their first audio at the p95 (from five turns). Every `call.started` names the worker that ran
it (`worker`).

The roster stops counting a worker as accepting when its minute is past the line
(`fleet/roster.py`): at least half the calls that ended in it ended in an error, over four calls
at least, or first audio's p95 is over five seconds, over twenty turns at least. It is left out
only while another worker of its fleet accepts and is not past the line, so the line never empties
a fleet: a box of one worker per world keeps counting its one, and a fleet whose every worker is
past it counts them all. Such a worker reads `failing` in `fleet list` and in `/metrics`
(`pinecall_worker_state`, with `pinecall_worker_first_audio_p95_seconds`), and the fleet's
`accepting` and `full` count without it. LiveKit's own dispatch is by load and does not read the
roster: the worker keeps its seats with LiveKit, and a worker that stays past the line is the
operator's to cordon. A worker of an older release carries no minute and is never past the line.

## Full, at the door

When every worker of a fleet is at the line, the token door answers `503`, writes `fleet.full` on
the agent's log, and names `POST /v1/callbacks`, so a page offers a call back before any room is
made. A phone caller arriving at a full production fleet is answered by the **overflow**, a worker
that is never full (`pinecall-overflow@production`): one sentence, `PINECALL_OVERFLOW_SAYS`, the
caller's number onto the agent's log as `callback.requested`, and it hangs up. No ears, no model.
An org's own concurrency is its `concurrent_calls` quota, counted on the calls this gateway serves
in that world and held at the door, never mid-call: sandbox calls never use up production's.

## Deploys drain, cordons shrink

A worker told to stop takes no new call and drains what it holds for up to ten minutes, then seals
what is left in sixty seconds; systemd waits fifteen. A **cordon** (`fleet cordon`,
`POST /v1/ops/fleet/{worker}/cordon`) is told on the worker's next heartbeat: it takes no new call,
finishes what it holds, and exits **3**, which its unit's `RestartPreventExitStatus=3` leaves down.

A deploy never closes a fleet. The box runs two workers per world and replaces one at a time
([../infra/box/README.md](../infra/box/README.md)): each comes back registered and heard before the
other is stopped. A fleet of machines is replaced by **generation**, with the loop's own verbs:

1. make the new generation's image (the new wheel, the same units and `fleets/<world>.env`) and
   point the cloud script at it (`PINECALL_FLEET_IMAGE`);
2. stop the loop, and run it once with `--min` at the machines up now plus the new ones wanted and
   `--grow-at-most` as many (`fleet loop --once …`): it creates them from the new image; wait
   until `fleet list` shows each new one `accepting`;
3. `fleet cordon` every machine of the old generation: each takes no new call, drains, and exits 3;
4. start the loop again with its usual flags: it deletes each cordoned machine once it has drained,
   and grows or shrinks the new generation by the numbers.

The loop is stopped while the new machines come up, or it would cordon the quietest of them as one
too many. At every step some machine takes new calls, and no call is moved.

## A worker that dies

A worker killed, a machine gone or a job's process dead leaves its callers in rooms with nobody
answering. LiveKit says so: its agent leaves without a goodbye (the connection lost, not closed),
and LiveKit's webhook tells the gateway (`POST /v1/livekit/webhook`). If the call is still open
and its caller still in the room, the gateway ends it as `drained`, puts a call back on the agent's
log for a phone caller, and sends the world's fleet into the room with a job that says one
sentence, the overflow's `PINECALL_OVERFLOW_SAYS`, deletes the room and seals the call. So the
caller hears that sentence, not silence: LiveKit notices a connection lost in seconds (it waits
five for it to come back), and the job starts on any worker with a seat, or on the overflow when
the fleet is full. On a box of one worker per world, a dead worker's calls wait for its unit to
come back (systemd restarts it in seconds), since the overflow opens only for a full fleet and a
fleet nobody hears from is not full. A drain, a cordon and a call that ends leave on purpose, and
are not this. If no job comes, the reaper seals the call after five quiet minutes, as before.

Measured on 2026-10-01 (a spoken call, its worker SIGKILLed 25 s in): **20.5 s** from the kill to
`call.ended drained` on the log — LiveKit's connection timeout for the agent, which leaves as
`CONNECTION_TIMEOUT` — and the sentence's job on the other worker a second later; before LiveKit
read its webhook on a box, the same call waited for the reaper's five minutes. The sentence is
said after `call.ended` is on the log: a client that hangs up on `call.ended` (the simulated caller
of `/v1/evals/voice` does) leaves before it; a phone caller stays on the line and hears it.

## The fleet loop

`pinecall-runtime fleet loop --cloud infra/fleet/gcp --seats 4 --fleet pinecall` keeps a fleet at a
target, **60 % busy** by default, `busy = active / seats` over the workers heard from, and holds
nothing between two ticks: the roster is the gateway's and the machines are the cloud's. Every
fifteen seconds (`fleet/hub.py`, numbers in, decisions out):

| when | it does |
|---|---|
| a cordoned machine holds no call, or went silent | **delete** it |
| a machine never dialled in within 10 min, or fell silent for 5 | **delete** it |
| fewer workers than `--min`, no seat anywhere, or busy over the target | **grow** by what is missing: `create pinecall-worker-<n>` for each machine |
| busy would still be under the target **by 0.15** without the quietest, and more than `--min` | **cordon** the quietest |

What is missing is counted in seats: those that bring busy back to the target (`active / target`,
the target read as the decimal it was typed as), less the seats there are, in whole machines of
`--seats`; under `--min`, the machines up to it; with no seat at all, one. The larger of these is
asked for, cut by `--max` and by `--grow-at-most` a tick (1 unless said: the loop as it was, one
machine a tick). A machine still booting counts as `--seats` of capacity from the moment it is
asked for, so the loop asks once and waits, and the slack keeps a grow and a cordon from chasing
each other. Shrinking stays one cordon a tick, and never in a tick that grows or while a machine
boots. The loop never cordons or deletes a machine the cloud does not list as
the fleet's: a worker stood up by hand counts and is never let go. `--once --dry-run` prints one
tick and touches nothing.

## The shape of the numbers

```
50 000 calls a day · 4 min a call · 10 busy hours · ×2 at the peak
  = 50 000 × 4 ÷ 60 ÷ 10 × 2  ≈  667 calls at once
```

At about 16 live calls a worker and 60 % busy, that is about 70 machines kept ready: `--max 70`.

The control plane, measured on 2026-10-01 with `pinecall-runtime load` from a machine of its own
(the golden call as the script, 2.1 entries a second per call, every log read back and verified):

| what | measured | so, per 1 000 calls at once |
|---|---|---|
| a gateway process | 1 200 calls on four processes: 3.6 cores | ~3 cores of gateway (~330 calls a core) |
| Postgres | 1.9 cores at 1 200 calls, the gateways on the same machine | the figure to plan with is the next-but-two row's: ~1.4 cores, measured with the gateways apart |
| Caddy, as the balancer on the box | 2.4 cores at 1 200 calls | ~2 cores: past one box, a cloud balancer |
| append, worker to log | p50 26 ms, p99 410 ms at 1 200 calls | — |
| a gateway killed every minute | 0 of 8 000 logs wrong; its calls go on on the others | — |
| gateways on a machine of their own, the box keeping Postgres | 2 000 calls on six gateways (two on the box, four apart): Postgres 2.6–3 cores, 9 017 calls sealed, 0 wrong | ~1.4 cores of Postgres, linear from 1 200 to 2 000 |
| a gateway machine killed for two minutes | 1 200 calls: 6 778 opened and sealed, 0 refused, 0 wrong, 0 billed twice | — |
| the media plane: two LiveKit nodes on one Redis | `lk load-test`, voice rooms of two audio publishers and one subscriber, rooms spread across both nodes: 400 at once, 0 packets lost, 2.25 cores a node | ~5–6 cores of SFU (~90 calls a core) |
| a worker on node 1 taking a room on node 2 | the agent dispatched to rooms created on either node: every job assigned and joined (livekit 1.13.7) | — |
| a machine of workers alone, the recording in the call's process (2026-10-02) | 24 calls on 8 vCPU: 3.5 cores, first audio p95 1.3 s, every turn answered | ~150 vCPU of workers (vCPU × 4 seats a machine) |
| the box as the media plane alone | 24 calls: 1.8 cores of SIP, LiveKit, gateways and Postgres | ~0.1 a call: a 4-vCPU box carries ~25 before a second media node |

What this says of a cell: Postgres grows by about 1.4 cores per 1 000 calls at once, so a cell of
100 000 would need some 140 cores of one database, which no one machine holds; a cell is sized
instead at 15 000–20 000 calls (a 32-core Postgres, ~50 gateway cores, ~1 000 worker machines) and
the platform grows by cells (P8 in `internal-docs`). What saturated first at 2 000 calls was not
Postgres but the box's own two gateway processes and its Caddy: past a box, gateways live on
machines of their own (`docs/a-box-in-production.md`, "Gateways on other machines") and the
balancer is the cloud's.
