# Scaling: one box to a fleet per world

The runtime is gateways on a cluster with workers on as many machines as the calls need. It is
configuration: a worker is `pinecall-runtime worker start` on a machine that
reaches the gateway and LiveKit, holding its world's fleet key. The operator's verbs are
[the-runtime-cli.md](the-runtime-cli.md); the cluster it runs on, from nothing, is
[../infra/README.md](../infra/README.md).

## Growing it, step by step

The runtime runs on a Kubernetes cluster (`infra/`): a core node for the box's services and a few
small workers, and a pool of worker nodes that grows with the calls. Each row is a value of the
chart or of Terraform, never a machine made by hand. The numbers are what the runtime was measured
holding on machines of the same types (the tables below); on the cluster they are proven on
staging before production.

| you need | do | holds (measured) |
|---|---|---|
| a cluster | `make tf-plan`, `make tf-apply`, `make image`, `make deploy` ([../infra/README.md](../infra/README.md)) | two gateways, Postgres, a LiveKit, a SIP and two small workers per world |
| more calls at once | nothing: KEDA adds a scaled worker when the gateway asks for one, and the cluster autoscaler a node for it ("The burst", below) | 32 calls a scaled worker, alone on an 8-vCPU node at ~60 % |
| more gateway processes | `gateway.replicas` in the chart's values | ~330 calls a gateway core |
| Postgres that outlives a node | `postgres.instances: 2` (`charts/postgres`): CloudNativePG keeps a streaming replica and promotes it; its WAL and nightly base backups are in a bucket either way | — |
| past ~15 000–20 000 calls | a second cell: another cluster, an org living in one | Postgres grows ~1.4 cores per 1 000 calls; one database is one cell |

## Three planes, each grows on its own

| plane | what it is | grows with |
|---|---|---|
| control | the gateways: logs, keys, routes, quotas, the roster; any serves any door of any call, telling the others what it did on Redis | requests, never calls: another gateway behind the balancer |
| media | LiveKit rooms, SIP, WebRTC: a LiveKit per world, so a sandbox call never shares a machine with a production one | one per world and region, beside the callers |
| workers | the conversations: the ears, the model, the voice | calls |

The control plane is as many gateways as the requests need. Each keeps in memory only a cache of
what Postgres holds and what is bound to a connection it holds (an app socket, a stream, a written
call's session, a WhatsApp thread) and, for five seconds, a key it verified; everything else is in
Postgres, or said on the signal each second (who holds which agent, the roster, an org's calls at
once). A key revoked, or a person changed (role, agents, production, operator, disabled), at a door is
forgotten on every gateway at once, said on the signal; a change made at a shell (`keys revoke`)
holds for those seconds on a gateway that remembered the key. Any gateway answers any request of
a call, so the cluster's load balancer sends each wherever it likes and a second gateway costs a
call nothing; the chart runs two (`gateway.replicas`), and a disruption budget keeps one up
through a node's drain. A release never shows a caller a `503`: a stopping gateway serves on for
30 s (its pod's `preStop`) while the load balancer takes it out of the group, and only then closes;
and it keeps an idle connection 620 s, past the load balancer's 600, so the load balancer never
sends a request down a socket the gateway already closed. A new gateway imports every vendor's
plugin before it listens (5 s), so no request waits on the first door that lists the vendors.

## A fleet per world

One gateway serves both worlds; what keeps a test call off a production process is **a fleet of
workers per world**. LiveKit dispatches by agent name, a worker registers under its unit's
`PINECALL_FLEET` (`pinecall` for production, `pinecall-sandbox` for the sandbox by default, the
`fleets` row of the box, `/v1/ops/fleets`), and the gateway dispatches every call to the fleet of
its world. A worker holds its world's fleet key, and the gateway refuses a call of the other world.
Everything below is per fleet: the roster is keyed by fleet, the totals are a fleet's, and the
cluster asks how many scaled workers each fleet wants.

## Capacity is counted in calls

A worker hands LiveKit one number, its load, and LiveKit reads it twice, with one line,
**0.7**: livekit-server offers a job only to a worker whose last **reported** load is under it
(`agents.target_load`, default 0.7; the worker reports every 2.5 s), and livekit-agents, in the
worker, declines at it (`load_threshold`, default 0.7). A call neither will take is **never
offered again**: if no other worker has room, it waits in its room in silence until the SIP
bridge closes it. So a worker speaks LiveKit's scale: one gated on CPU reports its share of the
machine, and one that counts its calls (`PINECALL_MAX_JOBS`) reports `0.7 × calls ÷ slots`, so
LiveKit's full is its last slot. To the gateway the same worker reports `calls ÷ slots`, full at
1.0, which is where the roster opens overflow. Neither of LiveKit's lines is overridden: they
are its contract, the same on LiveKit Cloud, and `tests/fleet/test_heartbeat.py` reads the
installed framework's to hold the worker to it.

(Until 2026-10-03 a counting worker reported `calls ÷ slots` to LiveKit too, and both of
LiveKit's readers called it full at 0.7 of its slots while the gateway counted the rest free: a
worker of 8 took six calls, a worker of 32 took 23, and the next rang in silence.)

The measure is taken, never guessed: on the machine type it will run on, calls with real audio,
more each step, until first audio's p95 crosses what a caller tolerates; `MAX_JOBS` is one under
it.

Two edges of LiveKit's remain, both a burst on a worker's last moment: the server reads a load up
to 2.5 s old, so in a burst faster than that it may offer a worker on its last slot one call too
many, which the framework declines and nobody retries; and a worker holding **no** call yet guesses
each call's weight as its line over its warm processes, so it accepts at most that many in the same
instant until the first is launched. With two workers or more, the server offers the declined call
to another; on the last free slots of the whole fleet it is silence until the roster says full
(five seconds) and overflow opens.

**Set it; never leave a worker on CPU.** Measured on 2026-10-01 with spoken calls (Deepgram,
Haiku, Cartesia) against a worker alone on an 8-vCPU machine: on CPU, ten calls placed a second
apart got five agents at 20 % of the machine — each new call's process loads the turn models, the
CPU reading crosses 0.7 for an instant, LiveKit marks the worker unavailable and never offers that
room again; counting (`PINECALL_MAX_JOBS=20`), 14 of 15 calls got their agent (the fifteenth was
the 0.7 line) at 42 % of the machine. **A call costs ~0.24 vCPU**, so a worker machine holds about
`2.9 × vCPU` calls at the line: set `PINECALL_MAX_JOBS` to `vCPU × 4` and the 0.7 line falls there.
That is a machine of workers alone. On a machine that also runs the box's services everything
shares the cores: the cluster's core node runs two workers of two seats in production and two of
one in the sandbox (`workers.core` in the chart's values), from the box measured whole:

**A box, measured whole.** On 2026-10-01, on a box of production's machine type (4 vCPU:
Postgres, LiveKit, SIP, the room's recorder of the time (egress), two gateways and one worker on
it), SIP callers speaking a turn every 10 s and the three vendors faked on their own wire on
another machine (the load lab): a call costs **~0.5 cores of the box** — ~0.3 the worker, ~0.12
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

**Where a call is recorded (2026-10-02).** Three ways, measured on the same lab:

| how | per call | memory | every voice |
|---|---|---|---|
| egress, one room composite (decode, mix, encode in a process of its own) | ~0.12 vCPU | ~80 MB | yes |
| egress, one track egress per voice (no transcode, a process per track) | ~0.2 vCPU (0.08–0.12 a track) | ~170 MB a track | yes |
| the call's own session (livekit's recorder, the voices already decoded there) | ~0.06–0.09 vCPU of the worker | — | yes: the room's other voices and the melody are laid in when it closes (the file decoded and encoded once more, ~1/40 of its length on a core, nothing held in memory) |

The session records: on a box alone it moves the cost rather than removing it (at six calls the
worker went from 1.78 to 2.31 cores and the recorder from 0.60 to 0, the box at 3.4 either way),
but it needs no egress at all, it scales with the workers wherever they run, and it is what the
next paragraph's machine of workers measured with.

**A machine of workers alone** (a worker machine joined to the box), the same callers and fakes,
the box keeping
the media plane and the gateways, the recording in each call's process and the file in the bucket:

| calls at once | worker machine (8 vCPU) | per call | the box (4 vCPU), no worker on it | turns answered | first audio p50 / p95 | ring to live |
|---|---|---|---|---|---|---|
| 8 | 1.5 cores | 0.19 | 1.5 cores | 96 of 96 | 1.23 / 1.30 s | 0.1 s |
| 16 | 2.8 cores | 0.17 | 2.3 cores | 185 of 208 | 1.23 / 1.30 s | 0.1 s |
| 24 | 3.5 cores | 0.15 | 1.8 cores | 274 of 274 | 1.23 / 1.30 s | 0.1 s |

**A call costs ~0.15–0.19 vCPU of a worker machine, its recording included**, so `vCPU × 4` seats
(32 on 8 vCPU) is ~60 % of the machine with every seat taken; and the box, holding SIP, LiveKit,
the gateways and Postgres for those calls, spends ~0.1 a call: a 4-vCPU box carries the media of
about 25 calls before it needs a LiveKit node or a SIP node of its own (above). First audio did
not move from 8 to 24: the worker machine was never the bottleneck of these calls, the box was
when the workers shared it.

**Two vCPU on each side** (2026-10-02, the load lab with an e2-standard-2 box and an e2-standard-2
worker, made and destroyed by Terraform): the calls that started all held, but no
step started more than six calls, whatever was asked: the worker had 8 slots and LiveKit refused
it at 0.7 of them (above, "Capacity is counted in calls"); read the rows as what six calls cost,
not as where two vCPU stop.

| asked | started | worker machine (2 vCPU) | per call | the box (2 vCPU), no worker on it | turns answered | first audio p50 / p95 |
|---|---|---|---|---|---|---|
| 8 | 5 | 1.35 cores | ~0.27 | 0.86 cores | 56 of 56 | 1.27 / 1.37 s |
| 10 | 6 | 1.60 cores | ~0.27 | 1.01 cores | 67 of 67 | 1.33 / 1.80 s |
| 12 | 6 | 1.57 cores | ~0.26 | 0.97 cores | 67 of 67 | 1.36 / 1.68 s |

On two vCPU a call costs the worker ~0.27 vCPU (more than on eight: the job processes' start and
the event loop weigh more on fewer cores) and the box ~0.16; six calls already put the worker
near 0.8 of the machine, so a 2-vCPU worker machine is not the shape to run a fleet on.

**The production shape, after the fix** (2026-10-03: box e2-standard-4, worker e2-standard-8
with its 32 slots, the lab made and destroyed by Terraform):

| calls asked | started | worker machine (8 vCPU) | per call | the box (4 vCPU) | turns answered | first audio p50 / p95 |
|---|---|---|---|---|---|---|
| 16 | 16 | 2.77 cores | 0.17 | 1.14 cores | 193 of 193 | 1.23 / 1.30 s |
| 24 | 24 | 4.14 cores | 0.17 | 1.72 cores | 288 of 288 | 1.23 / 1.31 s |
| 32 | 29 | 4.56 cores | 0.16 | 1.50 cores | 334 of 336 | 1.24 / 1.33 s |

Twenty-nine calls held at 57 % of the machine, first audio unmoved. The three that did not start
were refused by livekit-server, not by the worker: its log on the lab's box says, for each,
`failed to send job request: no servers available`. The worker had moved the framework's line to
every slot, but still reported `calls ÷ slots`, and the server cut at its own 0.7 of them, a few
calls late because the report is 2.5 s old. Reporting on LiveKit's scale (above) is the fix, and
the same lab measured with it, LiveKit's log clean of refusals:

| calls asked | started | worker machine (8 vCPU) | per call | the box (4 vCPU) | turns answered | first audio p50 / p95 |
|---|---|---|---|---|---|---|
| 24 | 24 | 3.73 cores | 0.16 | 1.51 cores | 284 of 284 | 1.23 / 1.30 s |
| 32 | **32** | 4.55 cores | 0.14 | 1.89 cores | 371 of 371 | 1.23 / 1.31 s |
| 32, two a second | **32** | 3.57 cores | 0.11 | 1.84 cores | 370 of 374 | 1.23 / 1.31 s |

**Every one of the 32 slots takes a call, at 57 % of the machine, first audio unmoved from 16
to 32**, and a burst twice as fast as the report to LiveKit (2.5 s) lost none: LiveKit's log over
the three runs, 88 jobs assigned, zero refused. The runs between the two tables that started 12 of 24 and 8 of 32 were a fault of the
lab, not of the worker: it destroyed each worker machine with its worker still up, and LiveKit
keeps such a worker registered for 15–20 minutes (below, "A worker that dies"), offering it calls
it cannot take. The lab now stops the worker first.

The same lab given 16 slots on a 2-vCPU worker (`--seats 16 --calls 8,12`) showed what an
oversized `MAX_JOBS` costs: the worker at 1.9 of its 2 cores, 78 of 89 and 95 of 127 turns
answered, first audio p95 3.9 s. (It started 8 of 8 and 12 of 12, which proves nothing about the
line: 12 of 16 is under the server's 0.7 of 16 plus the report's lag.)

## The gateway hears every worker

Every five seconds a worker posts its heartbeat, `{fleet, worker, agent_name, active, max_jobs,
load, draining}` and its last minute, `{ended, failed, errors, turns, first_audio_p95_s}`, and the
answer says whether it is cordoned and whether its fleet is full. A worker silent 30 s is no longer
capacity; one silent an hour is forgotten. The roster is in memory, on every gateway: each keeps the
heartbeats that reached it and says them to the others once a heartbeat, so a worker is counted on
every gateway whichever it beats on, and a cordon set on one stands on all (the newest setting of a
worker's cordon wins). A gateway that restarts has it back after one round of heartbeats; a
heartbeat's `full` is answered from totals at most a second old. `GET /v1/ops/fleet` and `fleet list` read it.

A worker registers with LiveKit under **its own name**, `<fleet>/<worker>.<its start>`
(`pinecall/worker-scaled-production-5f654f5f59-zwgjp.1791067258`),
and says it in each heartbeat as `agent_name` — only once LiveKit has registered it: a worker beats
from its start, and a machine still loading its plugins is counted but offered no call, since
LiveKit has nobody to give the job to. LiveKit offers a job only to the workers registered under the
name the job's dispatch carries, so a dispatch to that name reaches that worker and no other: the
gateway chooses (below, "Who takes a call"), not LiveKit's draw. The start is in the name because a worker can come back under the same
name — a pod's container started again after its node was reset — while LiveKit still holds the
last process's dead socket (below, "A worker that dies"): the roster keys by the worker's name, so
the gateway hears only the newest registration and never offers the dead one a call. Measured on
staging on 2026-10-03, before the start was in the name: two of sixteen calls offered to a worker
that had come back under its old name went unopened three times and reached the overflow.

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

## Who takes a call

LiveKit's own dispatcher offers a job once, to a worker drawn by the load each last reported,
and remembers nothing: a worker that declines, or a dead one LiveKit still lists (up to 20 minutes
for a machine that vanished, above), leaves the caller in silence
(livekit-server `pkg/service/agentservice.go`, `selectWorkerWeightedByLoad`; `pkg/agent/client.go`,
no retry). The gateway knows more, every worker's seats, calls and last heartbeat, five seconds old
at most, so the gateway chooses.

The SIP rule and a visitor's token keep dispatching to the fleet by its plain name, which no worker
holds, so LiveKit starts no job and keeps the dispatch on the room with the call's metadata. When a
person is alone in a room, LiveKit's webhook says so (`participant_joined`, or an agent's
`participant_left` after a hand-over), and the gateway reads the room's newest dispatch: one to a
fleet that nobody took is the call to place. The room is kept in `offers` until a worker opens its
call, an agent joins the room or the room ends, whichever LiveKit says first (`channels/offers.py`,
`gateway/dispatching/`): a caller who hangs up before any worker took the call leaves nothing to
offer, and a dispatch on a room that is gone would make the room again. An outbound call, a
simulated caller and the sentence of a worker gone are offered the same way, by the door that
makes them. So **the
gateway needs LiveKit's webhook** (`livekit.yaml`'s `webhook.urls`), each world's naming its world
(`?world=sandbox`), so the gateway asks the LiveKit the room is on: a box without it places no
call. A room is dispatched on the LiveKit of its fleet's world, which the `fleets` row says. The gateway offers it to the worker of the fleet heard in the last 12 s
with the most seats free, counting the calls offered to it and not opened yet, by its LiveKit name
(`<fleet>/<worker>`, above): no other worker can take it. A room no worker opened 12 s after its
offer is offered to another (LiveKit waits 10 s on a worker that does not answer; a live one opens a
call in about 2 s). After three offers, or when the workers of the fleet heard lately have no seat,
the room goes to the fleet's overflow (`<fleet>/overflow`, below): the caller hears one sentence and
a call back is written. A fleet none of whose workers was heard in 12 s is one a gateway that just
started knows nothing of yet (the next heartbeats, five seconds, tell it): its room waits for the
next sweep, for up to 12 s from the caller's arrival, and only then goes to the overflow. Any gateway sweeps the rooms every 3 s; an offer is taken by the update that finds the
count it read, so two gateways never offer one room twice. `fleet list` says each fleet's rooms
waiting for a worker in the last ten minutes, and `/metrics` as `pinecall_fleet{what="waiting"}`:
a number that is not 0 is a call that LiveKit or a worker dropped. Each offer is a line in the
gateway's journal, `room <call>: offered to <fleet>/<worker>: 3 seats free, heard 2 s ago`, so
`make logs | grep <call>` says who took a call and why; `doctor`'s `offers` line names a room kept
past a minute, which only a box with no gateway sweeping leaves.

## Full, at the door

When every worker of a fleet is at the line, the token door answers `503`, writes `fleet.full` on
the agent's log, and names `POST /v1/callbacks`, so a page offers a call back before any room is
made. A phone caller arriving at a full production fleet is answered by the **overflow**
(`pinecall-overflow@production`), registered as `<fleet>/overflow` and offered a call by the
gateway alone, when no worker has a seat or a room ran out of offers: one sentence,
`PINECALL_OVERFLOW_SAYS`, the caller's number onto the agent's log as `callback.requested`, and it
hangs up. No ears, no model.
An org's own concurrency is its `concurrent_calls` quota, counted on the calls this gateway serves
in that world and held at the door, never mid-call: sandbox calls never use up production's.

## Deploys drain, cordons shrink

A worker told to stop takes no new call and drains what it holds for up to ten minutes, then seals
what is left in sixty seconds; Kubernetes waits fifteen (the pod's `terminationGracePeriodSeconds`,
900). A **cordon** (`fleet cordon`, `POST /v1/ops/fleet/{worker}/cordon`) is told on the worker's
next heartbeat: it takes no new call, finishes what it holds, and exits **3**. In a cluster its
Deployment starts the container again under the same name, which is still cordoned, so a cordon
there is for looking, not for taking a worker out: a pod is taken out by deleting it
(`kubectl delete pod`), which stops its worker the same way, drained.

A deploy never closes a fleet. Each world's core workers are a Deployment that replaces one pod at
a time and never has fewer than it asks for (`maxUnavailable: 0`, `maxSurge: 1`): the new pod is
started and answering on its health port (its startup probe) before an old one is told to stop, and the old one drains its calls while the new one takes every new call of its
world. The scaled workers roll by Kubernetes' default, a quarter of them at a time, each old one
draining its calls as it goes. No call is moved.

## A worker that dies

A worker killed, a machine gone or a job's process dead leaves its callers in rooms with nobody
answering. LiveKit says so: its agent leaves without a goodbye (the connection lost, not closed),
and LiveKit's webhook tells the gateway (`POST /v1/livekit/webhook`). If the call is still open
and its caller still in the room, the gateway ends it as `drained`, puts a call back on the agent's
log for a phone caller, and sends the world's fleet into the room with a job that says one
sentence, the overflow's `PINECALL_OVERFLOW_SAYS`, deletes the room and seals the call. So the
caller hears that sentence, not silence: LiveKit notices a connection lost in seconds (it waits
five for it to come back), and the job starts on any worker with a seat, or on the overflow when
the fleet is full. On a fleet of one worker, a dead worker's calls wait for it to come back (its
Deployment starts it again in seconds), since the overflow opens only for a full fleet and a
fleet nobody hears from is not full. A drain, a cordon and a call that ends leave on purpose, and
are not this. If no job comes, the reaper seals the call after five quiet minutes, as before.

**A machine that vanishes is worse than a worker that dies.** A worker process that ends, even
killed, closes its connection, and LiveKit forgets it at once. A machine that disappears whole (a
VM deleted with the worker up, a kernel gone, a cable cut) closes nothing, and LiveKit 1.13.7 sets
no read deadline on a registered worker (`pkg/service/agentservice.go`, after registration): it
keeps pinging the dead socket until the kernel gives up, **15–20 minutes** measured on
2026-10-03, and all that time it keeps **offering that worker new calls**, drawn by its last
reported load, each offer waiting 10 s and then dropped with no retry. With one live worker
beside the ghost, about half the new calls of that fleet are lost in silence until the ghost is
gone. Every path the cluster drives — a release, KEDA letting a scaled worker go, `kubectl delete
pod` — stops the worker before its pod goes (SIGTERM, then its drain), so none of them makes a
ghost; a node lost to a fault does. Since the gateway chooses the worker (above, "Who takes a
call") the ghost takes nothing: a worker unheard for 12 s is never offered a call, and one offered
to it before that is offered again at 12 s. Measured on 2026-10-03 in the lab (two e2-standard-8
machines of 32 slots, 16 calls at one a second, the second machine powered off at once at the 8th
call): **16 of 16 started**; the 5 on the dead machine heard
the sentence and ended as drained; the 2 the gateway had offered it before it was known dead
were offered again at 12 s to the other and started; none went to the overflow, none was silent;
131 of 132 turns answered, first audio 1.23 / 1.31 s. With the gateway choosing, the same lab on
one worker machine held its numbers: 24 of 24 (first audio 1.23 / 1.31 s), 32 of 32 (1.26 /
1.40 s, 378 of 382 turns) and 32 of 32 at two a second (1.24 / 1.32 s, 370 of 370), every call
live 0.1 s after it rang.

Measured on 2026-10-01 (a spoken call, its worker SIGKILLed 25 s in): **20.5 s** from the kill to
`call.ended drained` on the log — LiveKit's connection timeout for the agent, which leaves as
`CONNECTION_TIMEOUT` — and the sentence's job on the other worker a second later; before LiveKit
read its webhook, the same call waited for the reaper's five minutes. The sentence is
said after `call.ended` is on the log: a client that hangs up on `call.ended` (the simulated caller
of `/v1/evals/voice` does) leaves before it; a phone caller stays on the line and hears it.

## The burst

The core node's workers hold a quiet hour's calls; every call beyond them goes to a **scaled
worker**, a pod of 32 seats alone on a node of the workers pool (e2-standard-8). How many of them
a fleet runs is one number the gateway computes from the roster it already hears,
`GET /v1/fleet/wanted?scaled=<prefix>&seats=<n>&most=<n>` → `{fleet, wanted, active, seats}`
(`fleet/demand.py`), and KEDA keeps the Deployment at it, asking every 15 seconds with the world's
fleet key, which names the fleet (never the operator's: a read of one number needs no more); `422`
without `scaled` and `seats`:

| when | the number |
|---|---|
| the core's seats keep busy (`active / seats`) at or under **60 %** | the scaled workers there are, or none |
| busy would pass 60 % | what brings it back under, in whole workers of `seats`, the core's seats counted first, cut at `most` |
| busy would stay under **45 %** (the target less 0.15) without one of them | one fewer |

The number is absolute: a pod still booting, not yet heard, is never asked for twice. Up is at
once; down is one pod every five minutes, never sooner than ten minutes after the last change, and
each pod let go drains its calls first (KEDA's `ScaledObject`, `charts/pinecall/templates/
workers.yaml`). A pod with no node to go to waits for the cluster autoscaler, which makes a node of
the pool for it and removes a node once it is empty; nothing else sizes the pool, and its ceiling
is Terraform's (`workers_max`). The ceiling of the pods is `workers.scaled.<world>.most` in the
chart's values.

Measured on staging on 2026-10-03, with the lab (`infra/lab/`: SIP callers with real audio from a
machine of their own, through the cluster's SIP node and LiveKit, the vendors faked on their own
wire), production's world, a turn every 10 s for two minutes, calls placed one a second:

| the step | started | turns answered | first audio p50 / p95 | scaled workers · nodes after |
|---|---|---|---|---|
| 4, on a cluster at rest | 3 of 4: the fourth reached the overflow | 33 of 33 | 1.32 / 2.22 s | 1 · 1 (KEDA asked for one at the third call) |
| 24 | 24 of 24 | 288 of 288 | 1.24 / 1.32 s | 2 · 2 |
| 32 | 32 of 32 | 383 of 383 | 1.24 / 1.38 s | 2 · 2 |
| 16, the node of the scaled worker holding the most calls reset at once at the 8th | 16 of 16, none to the overflow; the calls on that node heard the sentence and ended drained | 120 of 122 | 1.25 / 1.34 s | 2 · 2 |
| 16, a release of a new image (`make deploy`) while they were up | 16 of 16, none drained | every caller turn | 1.23 / 1.31 s | — |
| 8, Postgres's pod deleted 40 s in | 8 of 8, 0 errors | every caller turn | — | — |

A scaled worker spent 0.25–0.31 cores a call. The core node's four seats of production held on
their 4-vCPU node beside the box's services: two calls, first audio 1.24 / 1.32 s; four, 4 of 4
started and 46 of 46 turns answered at 1.26 / 1.39 s, the node at 57 %. The first step above, the
first calls after a release, saw LiveKit's VAD run slower than real time on those workers and a
p95 of 2.2 s; it did not come back. Postgres, deleted, stopped as
CloudNativePG stops it, a smart shutdown that waited 180 s for its clients: the gateways' open
connections went on serving and every call in flight was written whole, no new connection opened
for three minutes, and the database was back at 3 min 4 s; `charts/postgres` now waits 15 s.

The core node, lost (its VM deleted, 2026-10-04): the node pool made another, kubeip gave it the
same address, and the gateways answered again 7 minutes after the delete began, Postgres's disk
attached to the new node; calls started on it as before. Every call of the moment is lost with
the node: the core node's services are one of each.

Down, the same night with no call after 23:11 UTC: KEDA let the second scaled worker go at 23:17:53
and the last at 23:28:11, ten minutes apart as its window says; the cluster autoscaler deleted the
first empty node at 23:30 and marked the second at 23:38, the workers pool back to zero nodes.

## The shape of the numbers

```
50 000 calls a day · 4 min a call · 10 busy hours · ×2 at the peak
  = 50 000 × 4 ÷ 60 ÷ 10 × 2  ≈  667 calls at once
```

At 32 seats a scaled worker and 60 % busy, that is about 35 of them at the peak: `most: 35`.

The control plane, measured on 2026-10-01 with `pinecall-runtime load` from a machine of its own
(the golden call as the script, 2.1 entries a second per call, every log read back and verified):

| what | measured | so, per 1 000 calls at once |
|---|---|---|
| a gateway process | 1 200 calls on four processes: 3.6 cores | ~3 cores of gateway (~330 calls a core) |
| Postgres | 1.9 cores at 1 200 calls, the gateways on the same machine | the figure to plan with is the next-but-two row's: ~1.4 cores, measured with the gateways apart |
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
the platform grows by cells. What saturated first at 2 000 calls was not
Postgres but the box's own two gateway processes and the proxy in front of them, on the same
machine: in a cluster the gateways are pods of their own (`gateway.replicas`) and the balancer is
Google's.
