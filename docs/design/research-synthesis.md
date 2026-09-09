# Pinecall v2 — síntesis de la investigación y arquitectura final

> 2026-09-06. Cuatro reportes (memoria · retrieval · NOOA/harness · mercado), cruzados con
> lo verificado antes en el código de LiveKit, Pipecat, convo y Pinecall. Todo lo que se
> afirma tiene fuente en los reportes; lo que es propuesta está marcado como tal.

## 0. Las cinco decisiones que salen de la investigación

| # | decisión | evidencia |
|---|---|---|
| 1 | **LiveKit es el motor; Pipecat no entra.** | LiveKit cubre SFU + SIP (cualquier trunk) + SDKs móviles + AgentSession + grabación + dispatch. Lo único de Pipecat que no está en LiveKit es su runner YAML de evals — y ahí convo ya va por encima. |
| 2 | **Postgres es el único servicio con estado.** pgvector 0.8 + pg_textsearch (BM25 real, config `spanish`). | Retrieval y memoria recomiendan lo mismo por separado. Un contenedor sirve tenants, call log, KB y memoria. SQLite (convo) queda para tests. |
| 3 | **Memoria: interfaz propia con adapters; default construido sobre pgvector (~500 líneas).** | Ninguna librería publica retrieval < 150 ms de forma independiente. Mem0 OSS v2 quitó la consolidación (los hechos se acumulan) y mete spaCy inglés. Graphiti es el mejor cerebro (bi-temporal) pero exige Neo4j/FalkorDB y es solo Python → adapter opcional. Letta es un runtime, LangMem pre-1.0 muerto desde oct-2025. |
| 4 | **RAG: híbrido (dense + BM25 + RRF) es lo único que cabe en un turno de voz.** bge-m3 (MIT) en CPU vía TEI; sin reranker en el hot path. CRAG/agentic en background. | Presupuesto medido: 70–120 ms con bge-m3, 25–50 lite. Un cross-encoder de 0,6B en CPU rompe los 300 ms. GraphRAG: 15–25 s/query (LightRAG). |
| 5 | **Del harness de NOOA se roban cuatro cosas concretas** (abajo). Y una corrección: NOOA no reemplaza la historia por estado — la view va **al final** del prompt, la transcripción en el medio, el prefijo fijo adelante. | Paper 2607.20709 §context: static · events · dynamic. La ganancia de KV-cache depende de ese orden. |

## 1. Qué se roba de NOOA — y qué no

| idea NOOA | en Pinecall | veredicto |
|---|---|---|
| Docstring de clase = system prompt; docstring de método = prompt de la tool; campos interpolados `{self.x}` | ya casi: la descripción del `@tool` y las views con `{patient.name}` | **adoptar** |
| Tres regiones: prefijo estático · eventos append-only · bloques dinámicos al final | `<Knowledge>` + reglas fijas → transcripción → **view del stage al final** | **adoptar — corrige nuestro orden** |
| Previews acotados de resultados (`len=100, [:5]…`) en vez de volcados; la referencia vive en el objeto | el resultado de una tool se previsualiza y se valida antes de entrar al contexto; `this.slots` guarda todo | **adoptar** |
| Contrato tipado del retorno del modelo, con reintento sobre el error de validación | una view declara qué produce (`yields: { slot }`); `go()` solo pasa si valida | **adoptar** |
| `events.collapse(start, end, summary)` — compactar la vista, nunca el log | al cerrar un stage se colapsa su tramo en una línea; el call log con `seq` sigue entero | **adoptar** |
| Métodos `...` completados por LLM con loop CodeAct | una stage de voz es un bucle abierto con un humano, 1 llamada + tools por turno; CodeAct es inviable por latencia y sandbox | **no** — la idea sí (stage con retorno tipado), la mecánica no |
| Memoria ACT-R (relevancia · recencia · importancia) en un SQLite inspeccionable | el ranking de `recall()` usa las tres señales | **adoptar el ranking**, no el paquete |

## 2. La arquitectura, con las decisiones puestas

```
 web (widget) · WhatsApp · ☎ teléfono          ← el público. El código es para quien construye.
        │            │           │
        │ texto      │ texto     │ SIP → livekit-sip → livekit-server (SFU) → worker (livekit-agents)
        ▼            ▼           ▼
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ GATEWAY (FastAPI)                                                          │
 │  apps.py     WS del protocolo ↔ la clase del tenant (Node)                 │
 │  text/       sesiones chat y WhatsApp: mismo agente, sin audio             │
 │  log/        el ledger: seq · gap · caught_up · SSE · export OTel GenAI    │
 │  knowledge/  push por path · chunk por heading · bge-m3 + BM25 · RRF       │
 │  memory/     recall() ≤150 ms en el turno · remember() al colgar (1 LLM)   │
 │  evals/      rings 1-4 · DeterministicNode · consent · grounded · register │
 │  auth/       keys · tokens sellados · grants por canal                     │
 └────────────────────────────┬──────────────────────────────────────────────┘
                              ▼
                     POSTGRES 17 · pgvector · pg_textsearch        (el único estado)
                     tenants · routes · call_log · kb_chunks · contact_memories · eval_runs
```

El prompt de cada turno, en el orden que NOOA demostró:

```
[ estático, cacheado ]  <Knowledge> · <Rules> · protocolos · doc de las tools del stage
[ historia append-only ] la transcripción; stages cerrados colapsados en una línea
[ dinámico, al final ]   <Retrieved/> del turno · <Memory/> del contacto · la VIEW del stage · estado
```

## 3. Memoria — la interfaz

```python
class Memory(Protocol):
    async def recall(self, contact: str, query: str, k: int = 6, as_of: datetime | None = None) -> list[Fact]   # ≤ 150 ms, sin LLM
    async def remember(self, contact: str, turns: list[Turn], channel: str, at: datetime) -> list[Op]          # fuera de banda
    async def forget(self, contact: str) -> None
    async def history(self, contact: str) -> list[Fact]
```

- **`PgvectorMemory` (default):** tabla `contact_memories(contact_id, fact, category, embedding vector, tsv tsvector('spanish'), valid_from, invalidated_at, supersedes_id, source_call_id, confidence)`. `recall` = RRF en SQL filtrado por contacto y `invalidated_at IS NULL`, ranking relevancia·recencia·importancia. `remember` = una llamada LLM **por llamada, al colgar**, que recibe los hechos vigentes y devuelve ADD/UPDATE/INVALIDATE (el algoritmo Mem0 v1 de dos pasos, que Mem0 abandonó por costo por turno; por llamada el costo es nada y la calidad para un CRM es mejor). `forget` = `DELETE WHERE contact_id`.
- **`GraphitiMemory` (opcional):** para tenants que quieran razonamiento temporal ("¿qué plan tenía en marzo?"). Requiere Neo4j/FalkorDB.
- La identidad del contacto: número en teléfono/WhatsApp; `contactId` sellado en el token en web. Sin identidad, no se recuerda.

## 4. RAG — el stack por defecto

| capa | elección | perfil lite |
|---|---|---|
| store + léxico | Postgres + pgvector (HNSW, `halfvec`) + pg_textsearch (`spanish`) | igual |
| embedding | **bge-m3** (MIT) vía TEI `cpu-1.9` o fastembed ONNX int8 | multilingual-e5-small (384d) |
| fusión | RRF k=60, 30 candidatos por rama, top-8 al modelo | igual |
| reranker | ninguno en el hot path; FlashRank multilingüe opcional sobre top-10 | ninguno |
| chunking | H2/H3, tope 300–400 tokens, **ruta de headings prefijada** al texto | igual |
| ingesta background (opt-in por KB) | contextual chunks con LLM (Anthropic) · late chunking con bge-m3 | — |
| eval | ragas + DeepEval en CI; golden set de 50–100 preguntas por KB, recall@5 / nDCG@10 sin LLM | igual |
| hosted fallback | embeddings por API (OpenAI/Voyage/Cohere) documentado como "proprietary fallback" | — |

Presupuesto por turno: embedding 60–100 ms ‖ BM25 2–5 ms (en paralelo) → HNSW 3–10 → RRF < 2. **~70–120 ms.** El `<Retrieved/>` va en el bloque dinámico al final; cada retrieval deja `docs.sources` en el log → citas en la consola.

Descartados por licencia: jina v3 y jina-reranker (CC-BY-NC), ParadeDB (AGPL), Elasticsearch (AGPL).

## 5. Evals — cómo se supera a Coval y compañía

Lo que venden Coval ($100 / $500 / $4.500+ al mes), Hamming, Cekura ($0,25/min), Roark: simulación de llamadas, monitoreo post-hoc, revisión humana, 10–50 métricas por llamada. Cerrados. Miden **después**.

| capacidad | Coval / Hamming / Cekura | Pipecat evals · LiveKit testing | **Pinecall v2** |
|---|---|---|---|
| simulación de personas (texto y voz) | ✓ pago | ✓ YAML / pytest | ✓ `pinecall test`, `--voice` (agent.bridge), personas `apurado`/`spanglish` |
| **política determinista sobre acciones irreversibles** | ✗ | ✗ (aserciones de tool-call a mano) | ✓ derivada de `confirm:` — `DeterministicNode`, 0 llamadas al juez en el camino feliz. **Nadie más lo tiene** (reporte de mercado §4.1) |
| goldens por stage desde un estado | ✗ | ✗ | ✓ `{ state, input, expect }` |
| cada llamada real puntuada al colgar | ✓ monitoreo pago | ✗ | ✓ ring 4, 0,14 ¢ por llamada (medido en convo) |
| re-evaluar una llamada real con las mismas métricas | replay (Roark) | ✗ | ✓ ring 3 `pinecall eval <id>` |
| matriz de modelos sobre los mismos goldens | ✗ | ✗ | ✓ |
| ledger auditable por llamada con `seq`, replay, cursor | ✗ (spans) | ✗ | ✓ + export OTel GenAI (LiveKit 1.8 ya lo emite; no competimos) |
| revisión humana | ✓ | ✗ | ✓ consola: Board + Sessions con el consentimiento dibujado |
| código abierto | ✗ | ✓ | ✓ Apache 2.0 |
| precio | $100–4.500/mes | gratis | gratis; hosted por minuto |

Lo que hay que **agregar** para ganar de verdad (gaps que convo dejó anotados):
1. **Promoción automática** de una llamada real que falla ring 4 a golden candidato — hoy es manual.
2. **Métricas de conversación** en el nightly: p50/p95 e2e, interrupciones, WER de la persona (DeepEval `ConnectorTurn(latency_ms, interrupted)` ya lo trae).
3. **Deriva**: alerta cuando el score medio de ring 4 baja N puntos en 24 h.

Aviso honesto del reporte: Pipecat cerró parte de la ventana en cinco meses (`pipecat eval run` YAML, 1.4→1.8). La diferenciación defendible es la política determinista + el ledger + el estado por stage, no el juez.

## 6. Lo comercial

| capa | gratis | se cobra |
|---|---|---|
| framework (clase, views, tools, CLI, evals, consola) | Apache 2.0 | — |
| runtime self-hosted | `docker compose up` | — |
| **runtime hosted** `voice.pinecall.io` | — | por minuto de voz + por mensaje de texto; BYOK descuenta |
| **consultoría** | — | "tu agente + su suite de evals en dos semanas", sobre el framework abierto; cada cliente es un ejemplo público más |
| verticales | clínica y tienda como ejemplos | plantillas de contact center a medida (consultoría) |
| app hosting (Pinecall corre tu Node) | — | producto posterior, cuando un tenant no quiera operar nada |

El pitch para el público final es el canal, no el código: **un agente, tres puertas** — el widget web, el número de WhatsApp, el teléfono — con memoria del cliente entre las tres.

## 7. Cambios al plan de milestones

- **ms-0**: Postgres + pgvector + pg_textsearch en el compose desde el día uno (no SQLite).
- **ms-2**: el orden del prompt según NOOA (view al final) y `yields` tipado en la view.
- **ms-6**: sumar promoción a golden y deriva de ring 4.
- **ms-9**: `Memory` Protocol + `PgvectorMemory`; `knowledge/` con bge-m3 vía TEI en el compose; `GraphitiMemory` fuera del alcance de la primera release.
- Nuevo **ms-12**: export OTel GenAI del log (Langfuse ya integra LiveKit por OTel).

## 8. Fuentes

Los cuatro reportes completos están en los outputs de los agentes de esta sesión; las URLs clave: NOOA `github.com/NVIDIA-NeMo/labs-OO-Agents`, arXiv 2607.20709; pgvector, pg_textsearch (timescale), bm25s, bge-m3 (BAAI), TEI (huggingface), FlashRank; Mem0 docs `platform-vs-oss` y `migration/oss-v2-to-v3`, Graphiti `getzep/graphiti`, Zep CE discontinuado; Coval/Cekura/Hamming pricing; DeepEval 4.2.1 `metrics-conversational-dag`; OTel `semantic-conventions-genai`; EVA-Bench arXiv 2605.13841.
