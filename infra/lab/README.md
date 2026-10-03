# The lab: calls with real audio and no vendor, against a cluster

What a worker spends on a call is its own: the audio (RTP, resampling, Opus), the VAD and the turn
model, the streams to each vendor, the recording. The vendors' work is done on their machines. So
a load test that fakes the vendors on their own wire, timed like them, loads the cluster exactly as
real calls do and bills nothing: the runtime's plugins are the real ones, pointed at another URL
by the providers row. The callers are SIP, from SIPp, with audio, through the cluster's SIP node,
LiveKit and the workers, in production's world, whose scaled workers KEDA grows.

```console
$ cd infra/terraform/environments/staging
$ terraform plan -var lab=true -out=plan && terraform apply plan    # the generator, 5060 open to it alone
$ cd -
$ uv run --no-project python infra/lab/measure.py up                # configured, the agent connected
$ uv run --no-project python infra/lab/measure.py run --calls 4,24,32 [--rate 2] [--kill-at 8]
$ cd infra/terraform/environments/staging
$ terraform plan -var lab=false -out=plan && terraform apply plan   # the generator gone, 5060 closed
```

`up` points the providers row at the generator's fakes, makes the org `lab` with its production
quotas open, its three vendors keyed with a key that is not one, its hang-up judging off, and a key
of it that goes to the generator on stdin, never printed; the agent (`agents/examples/clinica-norte`)
runs there under `pinecall start --prod`, and the number `+15550100100` is hooked from the
generator's public address and approved by the operator. `run` places each step's calls at
`--rate` a second at the SIP node's public address, holds them two minutes, and prints a row per
step: the calls that started of those that rang, the production workers' cores (the pods' metrics,
averaged over the window) and per call, the core's services, turns answered, first audio p50 / p95,
ring to live, errors, calls ended as drained, and KEDA's scaled workers with the workers pool's
nodes at the end of the step. `--kill-at N` resets the node of a scaled worker at once at the first
step's N-th call, as a machine that dies.

| file | what |
|---|---|
| `measure.py` | the lab's two verbs, from a laptop: gcloud, kubectl and the gateway's doors |
| `generator.sh` | the generator's configuration, one verb a step, run on it over gcloud ssh |
| `fake_vendors.py` | Deepgram Flux, Cartesia and Anthropic, each on its own wire, timed like the vendor |
| `providers.json` | production's stages, each `base_url` at `FAKES_HOST`, no fallbacks: nothing can reach a real vendor |
| `caller.xml` | one SIPp caller: rings, speaks a turn every 10 s twelve times, hangs up |
| `caller.py` | writes `caller.pcap`, the turn as RTP: 2.5 s of a voiced tone, 7.5 s of silence |

The generator's SDP names its internal address and its INVITEs leave from its public one, which is
the network the number is fenced to: Terraform's `sip_sources` opens 5060 to it alone while it
stands.
