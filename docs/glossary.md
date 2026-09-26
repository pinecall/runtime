# Glossary

The tree speaks a dialect, and each word means one thing everywhere it is said — in a module's
name, a docstring, a door's sentence, a doctor's line. This page is the dictionary. When a word
here and the code disagree, the code is what happened and this page is the bug.

## The machine

| word | what it is |
|---|---|
| **box** | one machine running the runtime: the two processes, the media plane in containers, Caddy, the fence. `infra/box/` declares it; `PINECALL_ROLE` says which of the three shapes below it is |
| **hub** | a box holding the control plane and the media plane — gateway, SFU, SIP, Redis, Postgres — that workers on other machines dial. `all` is a hub with a worker on it |
| **worker** | the process that answers a call (a LiveKit job), and a box that runs only that, dialling its hub |
| **instance** | one runtime on a box: its own gateway, worker, database, fleet and keys, sharing the media plane, Caddy and the vendors' keys with the others. `production` is the one every box has; the `sandbox` is the second. `/etc/pinecall/instances/<name>.env` |
| **world** · **env** | production or the sandbox: two, never a third. `env` is the word on the wire and in a key (`pinecall-env`, `KeyRecord.env`); `world` is the box's (`PINECALL_WORLD`). One thing, said where the protocol speaks and where the box does |
| **fleet** | the name a worker registers under at the SFU, one per instance, so two instances on one SFU never take each other's calls; and the loop that adds and removes workers at a cloud |
| **overflow** | the one worker that is never full: it answers when every real one is, takes a number, hangs up |
| **fence** | `nftables` on the box, `infra/box/nftables.conf`: three host ports and what the media plane publishes |
| **credstore** | systemd's encrypted credentials, where every secret on a box lives; a unit loads its own by name |
| **peer** | the other instance — production's sandbox, a sandbox's production — and the fleet key each holds of the other |
| **core** | `pinecall-core`, `packages/pinecall-core`: `pinecall.types`, `pinecall.extensions`, `pinecall.errors` — the shapes and the points a policy plugs into, a distribution of its own on the standard library alone. The runtime depends on it; so does a policy (`cloud/`), which never installs the runtime |
| **port** · **adapter** | a store's contract and its two implementations: `<port>.py` holds the `Protocol` (`Orgs`, `Keys`, `Personas`), `<port>_memory.py` the in-memory adapter the unit ring runs on, `<port>_postgres.py` the one a box runs, with its SQL. `<port>_for(pool)` picks. `tests/test_ports_and_adapters.py` holds the convention |
| **public** | `pinecall/public/`: the pages a build copies into the wheel — the console, the widget — served by the gateway and never edited by hand |

## A request

| word | what it is |
|---|---|
| **door** | an HTTP or WebSocket endpoint of the gateway. `docs/protocol/every-door.md` lists every one |
| **knock** | one request at a door with a key; and the doctor's one request at a vendor with a key, to learn whether the key is alive |
| **corner** | the org, the world and the holder that one request resolves in: a key's scope. A tenant's key answers its own; the fleet's key answers the call's |
| **holder** | who a setting belongs to within an org: a person's own corner (`holder` = their id), or the org's (`""`). The three corners a key sees are its own, the team's and production's |
| **seat** | a member's place in an org, of which a plan grants so many; and a participant's place in a live call — the desk's, a supervisor's |
| **floor** | an org's live calls across every agent, as one stream; the box's floor is every org's |
| **hop** | one request the worker makes to the gateway, and what comes back |
| **grant** | what a key may hand another: a seat, a link, a verb |
| **verb** | one thing the product does, as a function of the domain package it changes — `sign_in_with_password`, `place_call`, `tuned_for`. A door calls one and wires what it returns; `docs/patterns.md` |

## A call

| word | what it is |
|---|---|
| **leg** | one SIP participant a call places: the caller's own, an outbound call, a transfer's second leg |
| **line** | the caller's audio in a live session: silenced while a tool runs or a supervisor speaks, hearing again after |
| **kit** | the vendors a call runs on — what it hears, decides and speaks with — built from the agent's declaration and the keys at hand |
| **lend** · **lending** | the box's own vendor keys an org may run a call on, per model; an org's own key is never refused |
| **standing** | how a vendor stands on this box, in one word: ready, no key, no plugin, or its own arrangement |
| **hold melody** | what an agent plays while a tool runs, published beside its voice so the recording hears it |
| **sealed** | a log closed after its `call.ended`: nothing is appended, readers have finished |
| **reaper** | the gateway's one job about calls it never ran: a call in a room no agent is in, ended so its log seals |
| **vault** | an org's secrets — provider keys, SSO, a carrier, its mail — sealed under `PINECALL_VAULT_KEY` |

## Evals

| word | what it is |
|---|---|
| **ring** | which tests run where: 0 needs nothing, 1 a database, 2 a model, 3 a voice, 4 the judges |
| **golden** | a fixed input with a known answer: the log that must reduce to one state, the questions a base or a memory must answer |
| **persona** | a synthetic caller, written by the org, that a suite dials an agent with |
| **register** | the tone an agent must keep — formal or familiar — and the check that reads it off the transcript |
| **verdict** | one word a check or a judge answers: `held`, `broken`, `deferred_verdict`, `skipped_verdict` |
