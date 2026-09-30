# The open stack

Three model servers on one NVIDIA GPU, and the providers row that points a box at them. The box
runs them through livekit plugins it already carries (`openai` for the LLM, the voice and the
embedder, `nvidia` for the ears), so nothing here is code: it is data the box is seeded with.

```
compose.yaml      Ollama (Gemma 4 12B, bge-m3), Kokoro-82M, NVIDIA Nemotron ASR Streaming
providers.json    the providers row for them; MODELS_HOST is the one word to replace
```

The walk, the numbers measured on an RTX 3090 and what moves them: [docs/the-open-stack.md](../../docs/the-open-stack.md).
