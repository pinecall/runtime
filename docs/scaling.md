# Scaling: one box to a fleet per world

The same runtime is one box that does everything, or a gateway with workers on as many machines as
the calls need. It is configuration: a worker is `pinecall-runtime worker start` on a machine that
reaches the gateway and LiveKit, holding its world's fleet key. The operator's verbs are
[the-runtime-cli.md](the-runtime-cli.md); the box is [../infra/box/README.md](../infra/box/README.md);
the clouds a fleet grows on are [../infra/fleet/README.md](../infra/fleet/README.md).

## Three planes, each grows on its own

| plane | what it is | grows with |
|---|---|---|
| control | the gateway: logs, keys, routes, quotas, the roster | requests, never calls |
| media | LiveKit rooms, SIP, WebRTC | one per region, beside the callers |
| workers | the conversations: the ears, the model, the voice | calls |

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

## The gateway hears every worker

Every five seconds a worker posts its heartbeat, `{fleet, worker, active, max_jobs, load,
draining}`, and the answer says whether it is cordoned and whether its fleet is full. A worker
silent 30 s is no longer capacity; one silent an hour is forgotten. The roster is in memory: a
gateway that restarts has it back after one round of heartbeats. `GET /v1/ops/fleet` and
`fleet list` read it.

## Full, at the door

When every worker of a fleet is at the line, the token door answers `503`, writes `fleet.full` on
the agent's log, and names `POST /v1/callbacks`, so a page offers a call back before any room is
made. A phone caller arriving at a full production fleet is answered by the **overflow**, a worker
that is never full (`pinecall-overflow@production`): one sentence, `PINECALL_OVERFLOW_SAYS`, the
caller's number onto the agent's log as `callback.requested`, and it hangs up. No ears, no model.
An org's own concurrency is its `concurrent_calls` quota, counted on the calls this gateway serves
and held at the door, never mid-call.

## Deploys drain, cordons shrink

A worker told to stop takes no new call and drains what it holds for up to ten minutes, then seals
what is left in sixty seconds; systemd waits fifteen. A **cordon** (`fleet cordon`,
`POST /v1/ops/fleet/{worker}/cordon`) is told on the worker's next heartbeat: it takes no new call,
finishes what it holds, and exits **3**, which its unit's `RestartPreventExitStatus=3` leaves down.

## The fleet loop

`pinecall-runtime fleet loop --cloud infra/fleet/gcp --seats 4 --fleet pinecall` keeps a fleet at a
target, **60 % busy** by default, `busy = active / seats` over the workers heard from, and holds
nothing between two ticks: the roster is the gateway's and the machines are the cloud's. Every
fifteen seconds (`fleet/hub.py`, numbers in, decisions out):

| when | it does |
|---|---|
| a cordoned machine holds no call, or went silent | **delete** it |
| a machine never dialled in within 10 min, or fell silent for 5 | **delete** it |
| fewer workers than `--min`, no seat anywhere, or busy over the target | **grow**: `create pinecall-worker-<n>` |
| busy would still be under the target **by 0.15** without the quietest, and more than `--min` | **cordon** the quietest |

A machine still booting counts as `--seats` of capacity from the moment it is asked for, so the
loop asks once and waits, and the slack keeps a grow and a cordon from chasing each other. At most
one grow or cordon a tick. The loop never cordons or deletes a machine the cloud does not list as
the fleet's: a worker stood up by hand counts and is never let go. `--once --dry-run` prints one
tick and touches nothing.

## The shape of the numbers

```
50 000 calls a day · 4 min a call · 10 busy hours · ×2 at the peak
  = 50 000 × 4 ÷ 60 ÷ 10 × 2  ≈  667 calls at once
```

At about 16 live calls a worker and 60 % busy, that is about 70 machines kept ready: `--max 70`.
