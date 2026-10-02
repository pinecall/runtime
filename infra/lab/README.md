# The lab: a worker's calls measured with real audio and no vendor

What a worker spends on a call is its own: the audio (RTP, resampling, Opus), the VAD and the turn
model, the streams to each vendor, the recording. The vendors' work is done on their machines. So
a load test that fakes the vendors on their own wire, timed like them, loads a worker exactly as
real calls do and bills nothing: the runtime's plugins are the real ones, pointed at another URL
by the providers row. The callers are SIP, from SIPp, with audio. `docs/scaling.md` has what it
measured.

| file | what |
|---|---|
| `fake_vendors.py` | Deepgram Flux (`WS /v2/listen`), Cartesia (`WS /tts/websocket`, `POST /tts/bytes`) and Anthropic (`POST /v1/messages`), each on its own wire, timed like the vendor |
| `providers.json` | production's stages (Haiku, Flux, Cartesia), each `base_url` at `FAKES_HOST`, no fallbacks: nothing can reach a real vendor |
| `caller.xml` | one SIPp caller: rings, speaks a turn every 10 s twelve times, hangs up |
| `caller.py` | writes `caller.pcap`, the turn as RTP: 2.5 s of a voiced tone, 7.5 s of silence |

## Three machines

A box of the machine type under test, made by `box up` from the wheel, with `.invalid` names; a
generator (8 vCPU) for the fakes, the agent and SIPp; and, for SIP × 2, a machine for a second
`livekit-sip`. All in one VPC, deleted when done.

1. **The box.** `sed s/FAKES_HOST/<generator>/ providers.json`, then `sudo pinecall-runtime
   providers seed` it. An org with its sandbox quotas open and judging off, a provider key that is
   not a key for each of `anthropic`, `deepgram` and `cartesia`, and a sandbox server token
   (`keys issue --env sandbox`) moved to the generator in a 0600 file, never printed.
2. **The fence**, for the generator alone: `nft insert rule inet pinecall input ip saddr <gen>
   tcp dport 8088 accept`, and the generator in `carrier_signalling` for 5060. The box's media
   ports are the cloud's firewall's (the box's network tag).
3. **The generator.** `uv run --with aiohttp --with numpy python fake_vendors.py 8700`; a `socat`
   from its own `127.0.0.1:8088` to the box's 8088, because Caddy's `http://127.0.0.1:8088` site
   answers any other Host with an empty 200; an agent project under `pinecall start` with
   `PINECALL_URL=http://127.0.0.1:8088` and the token; a number routed to it by
   `POST /v1/numbers {"hooked": true, "networks": ["<gen>/32"]}`.
4. **The webhook.** A box whose name resolves nowhere never gets LiveKit's webhook, and a dead
   worker's calls then wait five minutes for the reaper. Give LiveKit a way to the gateways (a
   site in `/etc/caddy/conf.d/` on the podman network's address, its port opened to `podman*`)
   before measuring one.
5. **Calls.** `uv run --with numpy python caller.py`, then
   `sipp <box>:5060 -sf caller.xml -s <number> -i <gen> -mi <gen> -m N -l N -r 1`. Raise N until
   first audio's p95 or the share of turns answered gives; read both from `call_log`
   (`turn.agent`'s `metrics.e2e_latency`, `turn.user` against `turn.agent`), and each unit's
   CPU from `systemctl show -p CPUUsageNSec` before and after.

## What fooled the first runs

- SIPp 3.7's `rtp_stream` sends 25–33 packets a second per call once there are several (50 is
  right), and every call offers the same media port: the caller's audio arrives broken, reads as
  barge-ins, and half the agent's turns are cut. `play_pcap_audio` holds 50 a second. 3.7 has no
  `-mp`; two SIPp processes side by side need their own `-p`.
- A box's `rtp_port` is a scalar, `10000-10199`: livekit-sip reads a map there as nothing and runs
  on its default range, which the box does not publish.
- A call's first job imports every installed plugin before its pipeline is live: the ring to
  live time grows with the box's load and is a number of its own, beside first audio.
