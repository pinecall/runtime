---
name: debug-a-call
description: Read a call's log to find what went wrong, and drive a simulated voice or text call at a gateway (local or a box) to reproduce it. Use for "the agent said nothing", a 503 from simulate, a slow turn, a tool that never answered, or proving a worker takes calls.
---

# Debugging a call

The log is the truth: every event of a call, with a `seq`, in `call_log`. Read it before reading
any process's stdout.

## Read the log

```bash
uv run pinecall-runtime sessions list                 # newest first, off Postgres, gateway up or not
uv run pinecall-runtime sessions show <call>          # entry by entry, every metric under its turn
uv run pinecall-runtime sessions tail <call>          # as it happens (polls)
uv run pinecall-runtime sessions recording <call>     # where the audio went (RECORD=1)
```

What to look for, in order: `call.started` (channel, direction) → `turn.user` / `turn.agent`
with their metrics (`e2e_latency`, `llm_node_ttft`, `tts_node_ttfb`) → `tool.call` /
`tool.result` (did the app answer? `timeout_s` on the ToolSpec) → `metrics.*` errors, the
session's own `error` entries → `call.summary` (usage, cost) → `call.score` (the judges: `panel`
is who RAN, `judges` who ANSWERED; `not_judged` means the session was built without a Scorer).

A call with `turn.user` and no `turn.agent`: the LLM or TTS — see the worker's journal. A call
with no `turn.user`: the STT, or the seat (`worker/seat.py`: which participant is the caller).

## Drive a call

From an example in the agents repo, against whichever gateway you name:

```bash
cd ~/pinecall-v2/agents/examples/clinica-norte
PINECALL_URL=https://box.pinecall.io node ../../bin/pinecall.js whoami        # which key, from where
PINECALL_URL=… node ../../bin/pinecall.js simulate --persona apurado --turns 3            # text
PINECALL_URL=… node ../../bin/pinecall.js simulate --persona apurado --voice --judge      # voice
node ../../bin/pinecall.js personas list
```

`--voice` mounts the class in THIS terminal (it serves the call the runtime opens), asks the
gateway to put a synthetic caller on a real line, and prints each turn with its latencies; with
`--judge`, `call.score` read back. The key comes from `PINECALL_API_KEY` in the env or
`~/.pinecall/credentials`; `PINECALL_URL` picks the gateway (otherwise the local dev file).

What a `503` from simulate means:
- `this box has no speech tool` — the GATEWAY's box lacks `espeak-ng` (macOS: `say`). It is in
  `PACKAGES`; `make deploy` installs it.
- `no agent joined room … in 20s: is pinecall-runtime worker up?` — no worker took the job:
  none registered on the SFU, or the one that did crashed on the job (its journal has a traceback:
  `NoProvider: <vendor> has no API key in this process` is a missing or empty credential).

## Which worker took it

```bash
ssh <hub> 'sudo podman logs --since 10m pinecall-livekit 2>&1 | grep -E "worker registered|job"'
ssh <worker> "sudo journalctl -u pinecall-worker --since '$START' -o cat" \
  | grep -iE "received job|pipeline is live|error|exception" \
  | sed -E 's/(key|secret|token)[=:] *[^ ]+/\1=***/Ig'
```

`received job request` names `job_id`, `dispatch_id`, `room`; `the pipeline is live N s after
the job arrived` is the number to watch (0.5 s on the box). To prove a call lands on ONE worker,
stop the others first (`sudo systemctl stop pinecall-worker` on the hub), never guess from load.

## Local

`uv run pinecall-runtime doctor` first. `unset PINECALL_API_KEY` before `pinecall run` against a
dev-key gateway (it is ignored out loud; a bare 403 is that). Native Postgres on the laptop:
`DATABASE_URL=…@[::1]:5432/…`. A `.env` the runtime ignored is never silent: the doctor's first
line says which file it read.

## Never

Print a key or a token from a journal, a `.env`, or `~/.pinecall/credentials` (the `sed` above
on every journal read). Run `simulate` against production with a persona that books: `consent`
is judged, but the tool still runs in the app you mounted.
