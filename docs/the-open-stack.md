# The open stack: your GPU as the box's vendors

A box names no vendor in code: every livekit plugin it carries is one, and which of them a call
runs on is the providers row. So a box can run on open models with nothing but data: three model
servers on your own GPU, and a row that points at them. No cloud vendor hears, thinks or speaks in
the call, and nothing a vendor bills. This page takes a machine with an NVIDIA card and nothing on
it to a call answered on open models.

| stage | model | server | how the box reaches it |
|---|---|---|---|
| ears | NVIDIA Nemotron ASR Streaming (0.6B, 40 locales, streaming) | NVIDIA's NIM container, Riva gRPC | the `nvidia` plugin, `server` and `use_ssl` in the row |
| the end of a turn | Smart Turn v3 (8 MB, 23 languages) | the worker's own CPU | `turn_model` in the row |
| thinking | Google Gemma 4 12B | Ollama, OpenAI-shaped | the `openai` plugin, `base_url` in the row |
| voice | Kokoro-82M | Kokoro-FastAPI, OpenAI-shaped | the `openai` plugin, `base_url` in the row |
| memory and knowledge | bge-m3 (1024 wide) | Ollama, OpenAI-shaped | the row's `embedding` |

Everything below is in the runtime's repository, `infra/models/`: `compose.yaml` runs the three
servers, `providers.json` is the row. Pinecall's own box runs on cloud vendors; this is for the box
you run.

## What you need

- **A box**, made as [from-zero.md](from-zero.md) or [a-box-in-production.md](a-box-in-production.md)
  make it (`pinecall-runtime box up`), doctor green.
- **An NVIDIA card with 16 GB of memory or more.** Everything loaded takes about 14 GB: the ears
  ~5, Gemma ~8.5, Kokoro ~1. It was measured on 24 GB (an RTX 3090); 16 GB fits with little room.
- **About 40 GB of disk** for the images and the models.
- **Ubuntu 24.04** on that machine. It can be the box itself or another machine the box reaches on
  a private network: the box only ever sees three ports.
- **A free NVIDIA account**, for the ears (step 2).

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

## 2. The key for the ears

The ears' image and model come from NVIDIA's catalog, which asks for a key; it costs nothing.

1. Sign up at [ngc.nvidia.com](https://ngc.nvidia.com), and open
   [org.ngc.nvidia.com/setup/api-keys](https://org.ngc.nvidia.com/setup/api-keys).
2. **Generate Personal Key**, any name, the service **NGC Catalog** ticked.
3. Copy it: it starts with `nvapi-` and is shown once. Never paste it in a chat or a file you share.

## 3. The servers

```bash
mkdir ~/pinecall-models && cd ~/pinecall-models
curl -fsSLO https://raw.githubusercontent.com/pinecall/runtime/main/infra/models/compose.yaml
curl -fsSLO https://raw.githubusercontent.com/pinecall/runtime/main/infra/models/providers.json

read -rs -p "NGC key: " k && printf 'NGC_API_KEY=%s\n' "$k" > .env && chmod 600 .env && unset k; echo
sed -n 's/^NGC_API_KEY=//p' .env | docker login nvcr.io -u '$oauthtoken' --password-stdin

docker compose up -d
```

`MODELS_BIND` is the address the servers listen on. Left out it is `127.0.0.1`, right when the box
is this machine. When the box is another machine, the private address it reaches this one at:
`MODELS_BIND=10.0.0.5 docker compose up -d`. **Never a public address**: the servers ask for no key,
so anybody who reaches them uses your GPU.

The first start is slow and only the first. Ollama pulls Gemma 4 (7.6 GB) and bge-m3; the ears
download their model and build an engine for your card, about fifteen minutes on a 3090. The
volumes keep it all, and a restart takes seconds. `docker compose logs -f nemotron-asr` shows
where it is.

## 4. Each server, checked

Each answers on its own; the box needs all three. With `H` the address from step 3:

```bash
H=127.0.0.1

# The ears: "ready" once the engine is built.
curl -s http://$H:9000/v1/health/ready

# The model: a word back, and the model named.
curl -s http://$H:11434/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"gemma4:12b","reasoning_effort":"none","messages":[{"role":"user","content":"Di hola"}]}'

# The voice: a WAV file you can play.
curl -s http://$H:8880/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"kokoro","voice":"ef_dora","input":"Hola, ¿en qué te puedo ayudar?","response_format":"wav"}' -o hola.wav

# The embedder: 1024 numbers.
curl -s http://$H:11434/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"model":"bge-m3","input":"hola"}' | head -c 120

nvidia-smi --query-gpu=memory.used,memory.total --format=csv   # about 14 GB used
```

## 5. The box, pointed at them

On the box. `MODELS_HOST` is the address from step 3 as the box reaches it (`127.0.0.1` when it is
the same machine). If the box is another machine, copy `providers.json` to it first.

```bash
sed 's/MODELS_HOST/127.0.0.1/g' providers.json > /tmp/providers.json
sudo pinecall-runtime providers seed /tmp/providers.json
```

`seed` writes the row a box starts from, once; a box that already has one refuses it, and the
console's **Box** screens edit what is there (or `PUT /v1/ops/providers` with the whole row).

The servers take no key, but the plugins refuse an empty one, so the box holds a placeholder for
its two vendors. In the console, **Box ▸ Keys**, `openai` and `nvidia`, any word; or on the box:

```bash
ops=$(sudo systemd-creds decrypt --name=PINECALL_OPS_KEY /etc/credstore.encrypted/PINECALL_OPS_KEY -)
for vendor in openai nvidia; do
  curl -fsS -X PUT "http://127.0.0.1:8080/v1/ops/provider-keys/$vendor" \
    -H "Authorization: Bearer $ops" -H 'Content-Type: application/json' -d '{"key": "local"}'
done
unset ops
sudo pinecall-runtime providers list | grep -E '^(openai|nvidia) '   # both: ready
```

## 6. A call

An org, a person and a project as [from-zero.md](from-zero.md) walks them. The smallest agent that
uses all three stages is one class, its prompt in the docstring, no tools:

```tsx
import { Agent } from "pinecall";

/**
 * Eres la recepción de la peluquería Rulos, en Montevideo. Hablas de vos, con frases cortas.
 * Todo lo que dices se lee en voz alta: sin listas, sin markdown, los números como se dicen.
 * Abrimos de martes a sábado, de diez de la mañana a siete de la tarde. El corte cuesta ochocientos pesos.
 */
export default class Recepcion extends Agent {
  language = "es";
}
```

`pinecall start` in the project, open the console's sandbox, the agent, and talk to it: the
browser asks for the microphone once. `pinecall sessions show <call>` then reads what the ears
heard, what the model said and how long each stage took.

## What the row says, and what to change

`providers.json`, line by line:

- **`stt/nvidia` `language_code`**: the locale the ears transcribe, for every call on the box. The
  NIM takes a full locale (`es-US`, `es-ES`, `en-US`, `pt-BR`, `fr-FR`…, 40 of them) or `auto` to
  detect it per call, and refuses a bare `es`; a locale the row names wins over the agent's
  language. Agents in several languages: `auto`.
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
| the ears' log: `Permission denied` on the manifest | the cache volume is not the image's user's | `docker compose up -d` again: `nim-cache-owner` fixes it before the ears start |
| the ears' log: `401` or `unauthorized` from `nvcr.io` | no key, or a key without **NGC Catalog** | a new key (step 2), `.env`, `docker login` again |
| `/v1/health/ready` not ready for many minutes | the first engine build | `docker compose logs -f nemotron-asr`; it prints each step |
| the agent is heard but never answers, the worker's log says `language code es doesn't match` | the row has no `language_code` | set it (above) |
| the first answer after a while takes ~20 s | the model was unloaded | the compose keeps it loaded (`OLLAMA_KEEP_ALIVE=-1`); an Ollama of your own needs the same |
| `nvidia-smi` shows Gemma partly on the CPU | not enough GPU memory beside the ears | keep Ollama's context at 8192 (the compose does) and nothing else on the card |
| `providers seed`: already configured | the box has a row | edit it in the console's Box screens |

## What it measured

An RTX 3090 (24 GB) holding the three servers, the box beside it; one caller line of 2.9 s, "Hola,
¿abren el sábado? ¿Y cuánto sale un corte?", four calls each:

| | livekit `v1-mini` | Smart Turn v3 |
|---|---|---|
| the turn's wait after the caller stops | 2.50 s | 0.93 s |
| the ears' final transcript after the caller stops | 1.03 s | 0.93 s |
| Gemma 4's first token | 0.68 s | 0.69 s |
| **caller stops → agent speaks** | **3.67 s** | **2.11 s** |

The ears transcribed every call word for word, and the agent answered right every time. A wait of
exactly 2.5 s is livekit's `max_delay`: the detector judged the turn unfinished, and the session
waited its longest. With Smart Turn the wait is the ears' own: the NIM finalizes a sentence about
0.9 s after the caller's last word, and that is the floor this stack stands on today.

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
