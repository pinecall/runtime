# The open stack: your GPU as the box's vendors

A box names no vendor in code: every livekit plugin it carries is one, and which of them a call
runs on is the providers row. So a box can run on open models with nothing but data: three model
servers on your own GPU, and a row that points at them. No cloud vendor hears, thinks or speaks in
the call, and nothing a vendor bills. This page takes a machine with an NVIDIA card and nothing on
it to a call answered on open models.

| stage | model | server | how the box reaches it |
|---|---|---|---|
| ears | Whisper large-v3-turbo (99 languages) | Speaches, OpenAI-shaped | the `openai` plugin, `base_url` in the row |
| the end of a turn | Smart Turn v3 (8 MB, 23 languages) | the worker's own CPU | `turn_model` in the row |
| thinking | Google Gemma 4 12B | Ollama, OpenAI-shaped | the `openai` plugin, `base_url` in the row |
| voice | Kokoro-82M | Kokoro-FastAPI, OpenAI-shaped | the `openai` plugin, `base_url` in the row |
| memory and knowledge | bge-m3 (1024 wide) | Ollama, OpenAI-shaped | the row's `embedding` |

Everything below is in the runtime's repository, `infra/models/`: `compose.yaml` runs the three
servers, `providers.json` is the row. Pinecall's own box runs on cloud vendors; this is for the box
you run.

## What you need

- **A box**, made as [from-zero.md](from-zero.md) makes it, doctor green.
- **An NVIDIA card with 12 GB of memory or more.** Everything loaded takes about 10 GB: Gemma
  ~8.5, the ears and Kokoro ~1 each. It was measured on 24 GB (an RTX 3090).
- **About 25 GB of disk** for the images and the models.
- **Ubuntu 24.04** on that machine. It can be the box itself or another machine the box reaches on
  a private network: the box only ever sees three ports.

## 1. The machine: driver, Docker, the container toolkit

On the machine with the card. Skip what `nvidia-smi` and `docker compose version` already answer.

```bash
# The driver; reboot once it is installed.
sudo ubuntu-drivers install && sudo reboot
nvidia-smi                                   # the card, its memory, the driver version

# Docker and its compose plugin, from Ubuntu.
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker "$USER" && newgrp docker

# NVIDIA's container toolkit, so a container is handed the GPU.
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker

docker run --rm --gpus all ubuntu nvidia-smi   # the same card, seen from a container
```

On the box itself, Docker stands beside the box's own containers: the box's firewall touches its
own table alone.

## 2. The servers

```bash
mkdir ~/pinecall-models && cd ~/pinecall-models
curl -fsSLO https://raw.githubusercontent.com/pinecall/runtime/main/infra/models/compose.yaml
curl -fsSLO https://raw.githubusercontent.com/pinecall/runtime/main/infra/models/providers.json

docker compose up -d
```

`MODELS_BIND` is the address the servers listen on. Left out it is `127.0.0.1`, right when the box
is this machine. When the box is another machine, the private address it reaches this one at:
`MODELS_BIND=10.0.0.5 docker compose up -d`. **Never a public address**: the servers ask for no key,
so anybody who reaches them uses your GPU.

The first start is slow and only the first: Ollama pulls Gemma 4 (7.6 GB) and bge-m3, and the ears
pull Whisper (1.6 GB). The volumes keep it all, and a restart takes seconds. `docker compose ps -a`
shows `ollama-models` and `whisper-model` as exited (0) once the models are in.

## 3. Each server, checked

Each answers on its own; the box needs all three. With `H` the address from step 3:

```bash
H=127.0.0.1

# The model: a word back, and the model named.
curl -s http://$H:11434/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"gemma4:12b","reasoning_effort":"none","messages":[{"role":"user","content":"Di hola"}]}'

# The voice: a WAV file you can play.
curl -s http://$H:8880/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"kokoro","voice":"ef_dora","input":"Hola, ¿en qué te puedo ayudar?","response_format":"wav"}' -o hola.wav

# The ears: that same WAV, written back as text.
curl -s http://$H:8000/v1/audio/transcriptions -F file=@hola.wav \
  -F model=deepdml/faster-whisper-large-v3-turbo-ct2 -F language=es

# The embedder: 1024 numbers.
curl -s http://$H:11434/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"model":"bge-m3","input":"hola"}' | head -c 120

nvidia-smi --query-gpu=memory.used,memory.total --format=csv   # about 10 GB used
```

## 4. The box, pointed at them

On the box. `MODELS_HOST` is the address from step 3 as the box reaches it (`127.0.0.1` when it is
the same machine). If the box is another machine, copy `providers.json` to it first.

```bash
sed 's/MODELS_HOST/127.0.0.1/g' providers.json > /tmp/providers.json
sudo pinecall-runtime providers seed /tmp/providers.json
```

`seed` writes the row a box starts from, once; a box that already has one refuses it, and the
console's **Box** screens edit what is there (or `PUT /v1/ops/providers` with the whole row).

The servers take no key, but the plugin refuses an empty one, so the box holds a placeholder for
its vendor. In the console, **Box ▸ Keys**, `openai`, any word; or on the box:

```bash
ops=$(sudo systemd-creds decrypt --name=PINECALL_OPS_KEY /etc/credstore.encrypted/PINECALL_OPS_KEY -)
curl -fsS -X PUT http://127.0.0.1:8080/v1/ops/provider-keys/openai \
  -H "Authorization: Bearer $ops" -H 'Content-Type: application/json' -d '{"key": "local"}'
unset ops
sudo pinecall-runtime providers list | grep '^openai '   # ready
```

## 5. A call

An org, a person and a project as [from-zero.md](from-zero.md) walks them. The smallest agent that
uses all three stages is one class, its prompt in the docstring, no tools:

```tsx
import { Agent } from "pinecall";

/**
 * Eres la recepción de la peluquería Rulos, en Montevideo. Hablas de vos, con frases cortas.
 * Todo lo que dices se lee en voz alta: sin listas, sin markdown, los números como se dicen.
 * Abrimos de martes a sábado, de diez de la mañana a siete de la tarde. El corte cuesta ochocientos pesos.
 */
export default class Recepcion extends Agent {}
```

`pinecall start` in the project, open the console's sandbox, the agent, and talk to it: the
browser asks for the microphone once. `pinecall sessions show <call>` then reads what the ears
heard, what the model said and how long each stage took.

## What the row says, and what to change

`providers.json`, line by line:

- **`stt/openai`**: the ears, Whisper on Speaches. Whisper does not stream: the session's voice
  detector cuts each sentence and the whole sentence is transcribed at once, in the agent's
  language. `use_realtime: false` keeps the plugin on that plain endpoint.
- **`voices`**: the voice per language, `openai/<language>`. Kokoro's Spanish voices are `ef_dora`,
  `em_alex` and `em_santa`, its English ones `af_heart`, `am_michael` and more; its list:
  `curl http://$H:8880/v1/audio/voices`.
- **`turn_model: smart-turn-v3`**: how the end of the caller's turn is read. Left out, it is
  livekit's own `v1-mini`. On this stack Smart Turn cut a second and a half (below); measure both on
  your agents and keep the better.
- **`llm/openai` `reasoning_effort: none`**: Gemma 4 thinks before it answers unless told not to;
  on a phone that is a second of silence before a one-line reply.
- **`rates` at zero**: a call on your GPU costs its org nothing a vendor bills. The console's cost
  columns read zero for these models.
- **`judge`**: the hang-up judges run on the same Gemma.

## When something does not come up

| what you see | why | what to do |
|---|---|---|
| `could not select device driver "nvidia"` | the toolkit is not configured for Docker | step 1's `nvidia-ctk runtime configure`, then restart Docker |
| the ears answer `404` for the model | the model is not downloaded yet | `docker compose ps -a`: wait for `whisper-model` to exit (0), or `docker compose up whisper-model` |
| the first turn of a call waits ~7 s | Whisper was not on the GPU: Speaches unloads an idle model | the compose keeps it (`WHISPER__TTL=-1`) and `whisper-model` loads it at start; a Speaches of your own needs both |
| the first answer after a while takes ~20 s | the model was unloaded | the compose keeps it loaded (`OLLAMA_KEEP_ALIVE=-1`); an Ollama of your own needs the same |
| `nvidia-smi` shows Gemma partly on the CPU | not enough GPU memory beside the ears | keep Ollama's context at 8192 (the compose does) and nothing else on the card |
| `providers seed`: already configured | the box has a row | edit it in the console's Box screens |

## What it measured

An RTX 3090 (24 GB) holding the three servers, the box beside it; a caller speaking five lines
with the agent's answers between them, Smart Turn reading the end of each turn:

| caller | what the ears heard | caller stops → agent speaks |
|---|---|---|
| Hola, buenas. ¿Abren el sábado? | Hola, buenas, abren el sábado. | 3.78 s (the first turn: Whisper was cold) |
| Bárbaro. ¿Y cuánto sale un corte? | Bárbaro y cuánto sale un corte. | 1.51 s |
| ¿Y el color, más o menos cuánto? | Y el color más o menos cuanto. | 1.57 s |
| ¿Puedo sacar un turno para el sábado a las once? | Puedo sacar un turno para el sábado a las 11. | 2.11 s |
| Dale, perfecto. Muchas gracias, chau. | Dale perfecto. Muchas gracias. Chao. | 1.64 s |

Of those ~1.6 s, the ears close the turn about 0.55 s after the caller's last word, Gemma 4's first
token takes about 0.7 s, and the voice's first audio 0.1–0.3 s.

**Why not NVIDIA's streaming ASR.** Nemotron ASR Streaming served by NVIDIA's NIM was this stack's
ears first: it closes a sentence in ~0.9 s and is exact on a single one. Over a call it is not: the
caller is silent for five to seven seconds while the agent speaks, and after two or three such
silences in one stream the NIM drops the next sentence whole or cuts it at its first pause. It
happens with NVIDIA's own Riva client as well as livekit's plugin, with background noise and with
any endpointing tried; with two-second silences all five lines come through. NVIDIA's own Pipecat
pipeline resets the recognizer at every sentence, which livekit's `nvidia` plugin does not. Until
one of them does, a stream that lives for a whole call is not safe, and Whisper, which transcribes
each sentence on its own, is.

## What it does not do yet

- **Tools the model must call.** Ollama does not honour `tool_choice: "required"`, so a model it
  serves may answer in words where a tool was demanded. The spoken caller of `pinecall simulate
  --voice` demands one and fails on Gemma with `502`; a written simulation works, and an agent's
  own tools are called when the model chooses to. A server that constrains the output to the call
  (llama.cpp's `llama-server --jinja`, or vLLM) fixes it; only the row's `base_url` changes.
- **Two calls at once** share one Ollama, which answers one request at a time per model by default.
  For more than a demo, serve the model with vLLM, OpenAI-shaped.
- **A box behind NAT** (a home line, a container) is not walked here: its SFU must offer an address
  callers reach (`rtc.node_ip` in `livekit.yaml`), and without inbound 443 its certificate must
  come by DNS (Caddy's `acme_dns`). A box on a cloud VM needs neither.
