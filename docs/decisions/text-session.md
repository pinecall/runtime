# The text session on livekit's AgentSession

An audit (§5, step 12) found `session/text/session.py` running its own
model → tools → model loop: `MAX_TOOL_ROUNDS`, a hand-kept `list[ChatItem]`
history, a `llm.chat()` per round. livekit-agents 1.8 already has all of
that. This card deleted
ours and made the session a **writer of the log over an `AgentSession`**.

## What runs now

Per call: one `TextAgent(livekit.agents.Agent)` — `instructions` = the static
prompt region, `tools` = the app's `ToolSpec`s as raw-schema function tools,
`llm` = the plugin `providers/models.py` built — and one
`AgentSession(llm=…, vad=None, max_tool_steps=8)`, started with
**no room and no job**: `await session.start(agent, record=False)`. livekit
builds no `RoomIO` (`start()` only builds one under `is_given(room)`) and
`get_job_context(required=False)` returns `None`, so nothing about a worker,
a recording or a track is reached. Nothing refused. `vad=None` is required:
without it livekit builds a silero inference client a text call never listens
to.

A caller's turn is `session.generate_reply(user_input=text)`, awaited on its
`SpeechHandle`. The tool loop, its bound, the retry with `tool_choice="none"`
on the last step, and the history are livekit's.

## What the call opens with

Before the first turn, `start()` seeds the history with
`clock.dated(context.today)` — the `current_date` call/output pair a voice
call opens with too (`pinecall/clock.py`, from `worker/entry.py`). Once per
call and never per turn: a caller who writes *mañana* needs the model to have
a calendar, and a system message appended mid-conversation would reach the
provider as something the caller said.

## The files

| file | the one idea |
|---|---|
| `session.py` | one call's life: start, the caller's turns, the app's commands, the log |
| `turns.py` | one reply: the turn in flight, what it says, and the entries that close it |
| `running.py` | one tool as livekit runs it: the app's process behind, the answer back |
| `declaring.py` | our `ToolSpec` as livekit's raw-schema tools |
| `pending.py` | the tool calls in flight over the app's socket, and their deadlines |
| `agent.py` | livekit's `Agent`: the view per request, the deltas, what the llm measured |
| `measure.py` | livekit's numbers as our wire carries them |
| `chat.py` · `connected.py` · `commands.py` | the caller's socket, who is connected, the app's commands |

`model.py` is gone: `Prompt` is session state and lives with it, `Metered` is
the llm_node seam's and lives there.

## The three regions, in livekit's terms

| region | where it lives | why |
|---|---|---|
| static | `Agent.instructions`, rewritten by `update_instructions` | built once per `prompt.set`; it is the cached prefix Anthropic's breakpoint lands on |
| history | the session's own `chat_ctx` | livekit owns it: user messages, assistant messages, `FunctionCall`/`FunctionCallOutput` pairs |
| view | appended inside `TextAgent.llm_node`, as the LAST item of the request's `ChatContext` | it is rendered from state and moves every turn, so it must sit outside the cached prefix |

tk-bdf12f joined static and view into one system message as a stopgap. That
ends here: `test_the_view_moves_without_touching_the_cached_instructions`
asserts the first system message is byte-identical across two turns while the
view changed, and that the view is the last item the model reads.

## Event → entry

livekit's `agent_state_changed` speaks five states (`initializing`,
`listening`, `thinking`, `speaking`, `idle`) and the wire pins three, so the
log is written from the session's own *path* — `llm_node` and the tool
callables, both of which run inline, in order, inside the activity's task —
rather than from the emitter, whose async handlers are scheduled as tasks and
would reorder the entries the ms-1 tests pin.

| moment on livekit's path | entry |
|---|---|
| the chat socket opens | `call.started` |
| `TextSession.hears()` | `turn.user` |
| `llm_node` entered | `agent.state thinking` |
| first content delta of a request | `agent.state speaking` |
| every content delta | `agent.transcript` (`final=false`, ephemeral) |
| the request's stream closed (livekit's `metrics_collected` on the LLM) | `metrics.llm` — `LLMMetrics.model_dump(exclude_defaults=True)`, unchanged |
| our tool callable, before the app | `tool.call` |
| the app's `tool.result` | `tool.result` |
| the `SpeechHandle` completes | `turn.agent` with the turn's ttft/tps from that reply's `LLMMetrics` |
| after `turn.agent` | `agent.state idle` |
| `call.hangup` | `call.ended`, then `call.summary` |

Text deltas **are** available in text mode: `llm_node` yields the raw
`ChatChunk`s, so `agent.transcript` is still per delta and no assertion had to
be weakened. (`transcription_node` and `session.output.transcription` are the
other hook; `llm_node` was chosen because the view and the metrics need the
same seam.)

`call.summary`'s usage rows are `AgentSession.usage.model_usage` — livekit's
`ModelUsageCollector`, whose `LLMModelUsage` declares exactly the field names
our wire model declares, so the rows are dumped and revalidated. **`Spend` is
deleted**: nothing of ours adds a token up any more.

## Inside a tool

livekit executes function tools itself, so the platform's place is the tool's
own callable. Each raw-schema tool's body is ours and takes `raw_arguments`
plus a `RunContext` (livekit injects it by type hint, which is where the
`call_id` comes from): it writes `tool.call`, waits for the app's own process
until the tool's declared timeout, writes `tool.result`, and returns the text
the model reads — or raises livekit's `ToolError`, which is how `is_error` is
set on the output. Nothing stands in front of it: the confirmation gate that
once did was removed with this milestone, and `docs/decisions/confirm.md` is
the record of why and of what the wire still carries.

## What could not be preserved, and why

- **`session.aclose()` runs first in `hangup()`.** Closing after the log is
  sealed abandons the activity's own `on_exit` coroutine, which surfaces as an
  unraisable warning in an unrelated test.
- **A tool that failed** now reaches the model through `ToolError`, which is
  how livekit sets `is_error` on the output; before, the session set the flag
  itself.

## What convo taught

`~/prueba-abai/convo/session/` writes its log from the session's *hooks*, not
from a subscription: the place that already runs in order is the place the
entry belongs. That is the whole shape of this card. What is different: convo
mapped livekit's own agent states onto its log, and this wire pins three
states of its own, so ours are written at our seams.
