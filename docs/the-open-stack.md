# The open stack: your GPU as the box's vendors

A box names no vendor in code: every livekit plugin it carries is one, and which of them a call
runs on is the providers row. So a box can run on open models with nothing but data: three model
servers on a GPU, and a row that points at them. This page is that walk, from a GPU to a call
heard, answered and spoken with no cloud vendor in it.

| stage | model | server | how the box reaches it |
|---|---|---|---|
| ears | NVIDIA Nemotron ASR Streaming (0.6B, 40 locales) | NVIDIA's NIM container, Riva gRPC | the `nvidia` plugin, `server` and `use_ssl` in the row |
| the end of a turn | Smart Turn v3 (8 MB, 23 languages) | the worker's own CPU | `turn_model` in the row |
| thinking | Google Gemma 4 12B | Ollama, OpenAI-shaped | the `openai` plugin, `base_url` in the row |
| voice | Kokoro-82M | Kokoro-FastAPI, OpenAI-shaped | the `openai` plugin, `base_url` in the row |
| memory and knowledge | bge-m3 (1024 wide) | Ollama, OpenAI-shaped | the row's `embedding` |

## 1. The GPU

One NVIDIA card with 16 GB or more (everything loaded takes about 14 GB), its driver, and the
NVIDIA container toolkit so Docker hands containers the GPU. The models may run on the box itself
or on another machine the box reaches; either way the box only sees three ports. The box's
firewall touches its own table alone, so Docker beside it is fine.

The ears' image and model come from NVIDIA's catalog: a free account at ngc.nvidia.com, then
Setup ▸ API Keys ▸ **Generate Personal Key** with the **NGC Catalog** service. The key starts with
`nvapi-`; it is shown once.

## 2. The servers

```bash
mkdir pinecall-models && cd pinecall-models
curl -fsSLO https://raw.githubusercontent.com/pinecall/runtime/main/infra/models/compose.yaml
curl -fsSLO https://raw.githubusercontent.com/pinecall/runtime/main/infra/models/providers.json
read -rs -p "NGC key: " k && printf 'NGC_API_KEY=%s\n' "$k" > .env && chmod 600 .env && unset k
docker login nvcr.io -u '$oauthtoken' --password-stdin < <(sed -n 's/^NGC_API_KEY=//p' .env)
docker compose up -d
```

`MODELS_BIND` is the address the servers listen on: `127.0.0.1` by default, for a box on this same
machine; `MODELS_BIND=10.0.0.5 docker compose up -d` for a box elsewhere on a private network.
Never a public address: the three servers ask for no key.

The first start is slow and only the first: Ollama pulls Gemma 4 (7.6 GB) and bge-m3, and the NIM
downloads its model and builds its TensorRT engine for your card, about fifteen minutes. The
volumes keep all of it. Ready when this answers `ready`:

```bash
curl -s http://127.0.0.1:9000/v1/health/ready
```

## 3. The row

On the box, with `MODELS_HOST` the address the models listen on:

```bash
sed 's/MODELS_HOST/127.0.0.1/g' providers.json > /tmp/providers.json
sudo pinecall-runtime providers seed /tmp/providers.json
```

`seed` writes the row a box starts from, once; after it the console's Box screens edit it, or
`PUT /v1/ops/providers` whole. The servers take no key, but the plugins refuse an empty one, so the
box holds a placeholder for its two vendors (the console's Box ▸ Keys, or on the box):

```bash
ops=$(sudo systemd-creds decrypt --name=PINECALL_OPS_KEY /etc/credstore.encrypted/PINECALL_OPS_KEY -)
for vendor in openai nvidia; do
  curl -fsS -X PUT "http://127.0.0.1:8080/v1/ops/provider-keys/$vendor" \
    -H "Authorization: Bearer $ops" -H 'Content-Type: application/json' -d '{"key": "local"}'
done
unset ops
sudo pinecall-runtime providers list --does stt    # nvidia: ready
```

What the row says, and why:

- `stt/nvidia` `language_code: es-US`: the NIM takes a full locale (`es-US`, `es-ES`, `en-US`, `pt-BR`…,
  or `auto`) and refuses a base code; a code the row names wins over the call's. Change it to the
  language your agents speak.
- `turn_model: smart-turn-v3`: the end of the caller's turn read off the audio by Smart Turn v3.
  Left out, it is livekit's own `v1-mini`. Measure both on your box (below) and keep the faster.
- `llm/openai` `reasoning_effort: none`: Gemma 4 thinks before it answers unless told not to; on a
  phone that is a second of silence for a one-line reply.
- `rates` at zero: a call on your own GPU costs its org nothing a vendor bills.

## 4. A call

The first org and an agent as [from-zero.md](from-zero.md) walks them. The smallest agent that
exercises all three stages is one class and a docstring:

```tsx
/**
 * Eres la recepción de la peluquería Rulos, en Montevideo. Hablas de vos, con frases cortas.
 * Todo lo que dices se lee en voz alta: sin listas, sin markdown, los números como se dicen.
 * Abrimos de martes a sábado, de diez de la mañana a siete de la tarde. El corte cuesta ochocientos pesos.
 */
export default class Recepcion extends Agent {
  language = "es";
}
```

`pinecall start`, then talk to it from the console or the widget.

## What it measured

An RTX 3090 (24 GB) holding all three servers, the box beside it; one caller line of 2.9 s, "Hola,
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

- **The simulated caller of a voice simulation** (`pinecall simulate --voice`) needs a model that
  calls a tool when told it must (`tool_choice: required`). Ollama does not honour that, so the
  spoken caller fails on Gemma with `502`; a written simulation works. Give the persona an `llm`
  served by something that honours it (vLLM does), or a hosted model.
- **Two calls at once** share one Ollama, which answers one request at a time per model by default.
  For more than a demo, serve the LLM with vLLM, OpenAI-shaped: only the row's `base_url` changes.
- **A box behind NAT** (a home connection, a container) is not walked here yet: its SFU must offer
  an address callers reach (`rtc.node_ip` in `livekit.yaml`) and its names must reach Caddy on 443
  for a certificate. A box on a cloud VM, as [a-box-in-production.md](a-box-in-production.md)
  makes it, needs neither.
