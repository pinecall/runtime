# Scaling: one server to a fleet

The same runtime is one process on a laptop, one server that does everything, or a control plane
with workers in as many regions as you need. It is **configuration, not a rewrite** — the machine
you deploy is `PINECALL_ROLE` in a file, and nothing else moves.

This page is what is true in the code today, marked **built** or **next release** line by line, so
nobody reads the landing page as a promise the runtime already keeps. The operator's verbs are
[the-runtime-cli.md](the-runtime-cli.md); the box that runs all of this is
[../infra/box/README.md](../infra/box/README.md).

## Three planes, each grows on its own

| plane | what it is | grows with |
|---|---|---|
| **Control plane** | the gateway: call records, keys, routes, quotas | requests, never calls |
| **Media plane** | LiveKit rooms, SIP trunks, WebRTC | one per region, next to the callers |
| **Workers** | the conversations: speech, the model, the voice | one to thousands, each a number of calls |

One machine runs all three (`PINECALL_ROLE=all`). A **hub** runs the control and media planes and
holds no calls; a **worker** holds nothing but calls and dials the hub by name. That is the whole
of the split — **built**, in `infra/box/`.

## Capacity is counted in calls, never in CPU — built

Each worker reports one number to LiveKit: the share of its seats in use, **live calls over the
calls it was measured to hold** (`worker/load.py`, `SlotLoad`). LiveKit routes each new call to the
worker with the most room, and stops routing to one at **0.7** of its seats.

```
a worker's load  = active calls / PINECALL_MAX_JOBS
the fleet's free  = Σ (max − active)   over the workers that are up
```

`PINECALL_MAX_JOBS` is **measured, never guessed**: on the machine type it will run on, calls with
real audio in a loop, five more each step, until the p95 of first audio crosses 1.8 s. That
concurrency is the ceiling, and `MAX_JOBS` is one under it, because LiveKit re-reads the load every
half second and two calls inside that window both see the old count.

> A machine's CPU measures its neighbours as well as its calls, which is how a shared box stops
> taking calls at the fourth one. **Seats measure only what a worker was built to hold** — so a
> worker unset with `MAX_JOBS` falls back to the CPU average, which is right for a box it shares
> with the SFU and wrong for one it has to itself.

`free = Σ(max − active)` is the number a person watches and the number a loop will scale on. The
number exists and is read on every dispatch today; **the loop that acts on it is next release** (below).

## Workers that dial out — built

A worker opens **one outbound connection** to the hub and asks for calls. No public address, no
open port but ssh, no load balancer to configure (`infra/box/nftables.conf`, `pinecall-worker.service`).
Add a worker and it takes calls within minutes; its media goes to the hub's public UDP port, its
control to `PINECALL_GATEWAY_URL`, and it knocks with an org key minted on the hub.

A worker on a full box (`role=all`) keeps its own health server on **loopback:8082**
(`PINECALL_WORKER_HTTP_PORT`) — never the gateway's 8080 or the embedder's 8081, which share the
machine (fixed 2026-09-11; a full box could not start a worker before).

## Deploys that drain, not cut — built

A restart is a drain. On `SIGTERM` the worker tells LiveKit it is full and finishes every call it
holds; systemd gives it **fifteen minutes** of grace (`TimeoutStopSec=900`) where its default 90 s
would cut a call mid-sentence. `make deploy` restarts the gateway first and the worker only once
the gateway answers, so no call rings into the gap.

## Concurrency per client, held at the door — built

What a plan sells is one number — the calls a tenant may hold at once — and it is enforced
**before a worker is spent**, not after. `Admission.a_call` refuses a call past the org's
`concurrent_calls` quota, on the control plane, the same way on one server or a thousand
(`orgs/admission.py`). A tenant over the line is refused with a sentence and `credits.exhausted` in
its own log; nothing already inside the call is cut.

## One boot file, any cloud — built

Every server boots from the same standard file — `infra/box/cloud-init.yaml` — on any provider or
from a USB stick. Hand it to the instance as user-data with three lines filled (ssh key, domain,
the SFU's public URL); it installs podman, caddy, nftables and make, and systemd brings the rest
up in order. The cloud only ever creates and deletes machines; **when and which is the runtime's,
the same way everywhere.**

---

## What is next release

These are named on the landing page and are **not in the code yet**. They all read the one number
the fleet already reports (`free = Σ(max − active)`); none of them is a rewrite of anything above.

| | what it will do |
|---|---|
| **The fleet loop** | keep workers ~60 % busy: when the free seats cross the line, request a server; it boots from the same file and dials the hub, taking calls in minutes |
| **Cordon** | a server no longer needed takes no new calls, finishes the ones it holds, and only then is deleted — the graceful shrink, above the per-restart drain that already exists |
| **Overflow at the door** | when every seat is taken: the web widget offers a call back before a room is made; a phone call is answered by an overflow agent on the control plane, which never fills, and called back |
| **Media in every region** | a media plane next to the callers, so no turn crosses an ocean to be heard |
| **Per-cloud machine scripts** | ~40 lines per provider — create, delete, list — the only cloud-specific code, with no Kubernetes and no autoscaler that decides by CPU |

The order that makes sense to build them, and why: **cordon** and **overflow at the door** first
(they protect calls already inside, and both are decisions on numbers the runtime already holds),
then the **fleet loop** (it needs the per-cloud create/delete under it), then **media per region**.

## The shape of the numbers

The landing's worked example, for reference — the arithmetic, not a benchmark:

```
50,000 calls a day · 4 min a call · 10 busy hours · ×2 peak-over-average
  = 50000 × 4 ÷ 60 ÷ 10 × 2  ≈  667 calls at once at the peak
```

At ~16 live calls a worker and 60 % target busy, that is ~70 servers kept ready — the fleet loop's
job, once it exists. Today the runtime **holds** that many calls if you stand the workers up by
hand or by your own script; what is next release is the loop that stands them up **for** you.
