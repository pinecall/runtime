# Events: the app's side

What the platform and the app say to each other about tools, state, confirmation, memory and retrieval, and the lines the app writes itself. The index is `events.md`.

### `confirm.declined`

The caller did not say yes, or the request lapsed. The tool does not run; the model is told.

| field | type | required | meaning |
|---|---|---|---|
| `tool` | `string` | yes | The tool that will not run. |
| `call_id` | `string` | yes | The tool call refused. |
| `audience` | `string` | yes | What was asked, repeated from the request. |
| `said` | `string` | no | What the caller said, when they said anything. |
| `reason` | `"no" | "timeout" | "changed" | "cancelled"` | yes | no: they refused. timeout: ttl ran out. changed: they asked for something else. cancelled: the call moved on. |

### `confirm.granted`

The caller said yes. The platform minted a one-shot token bound to the audience and the tool now runs. The token itself never enters the log.

| field | type | required | meaning |
|---|---|---|---|
| `tool` | `string` | yes | The tool that may now run. |
| `call_id` | `string` | yes | The tool call released. |
| `audience` | `string` | yes | What the token is bound to, repeated from the request. |
| `said` | `string` | yes | What the caller actually said. |
| `ttl_s` | `integer` | yes | How many seconds the token stays valid. |

### `confirm.request`

A tool with confirm set is about to run and the platform is asking the caller. The agent reads the phrase; nothing runs until confirm.granted.

| field | type | required | meaning |
|---|---|---|---|
| `tool` | `string` | yes | The tool waiting for a yes. |
| `call_id` | `string` | yes | The tool call waiting. |
| `arguments` | `object` | yes | The arguments the yes will be bound to. |
| `audience` | `string` | yes | sha256 of tool plus canonical arguments: the one thing the token will be good for. |
| `phrase` | `string` | yes | What the agent reads back: the action in the caller's words. |
| `ttl_s` | `integer` | yes | How many seconds the caller has to answer before the request lapses. |

### `custom`

A line the app wrote into the log with call.log. The platform never reads it; the console shows it and evals may.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The app's name for the line: slot_search, handoff_note. |
| `data` | `object` | yes | Whatever the app attached. |

### `docs.sources`

What retrieval put in front of the model for this turn. An answer can be traced back to its chunks.

| field | type | required | meaning |
|---|---|---|---|
| `query` | `string` | yes | What was searched for. |
| `sources` | `DocSource[]` | yes | The chunks, best first. |
| `took_ms` | `number` | yes | How long retrieval took, milliseconds. |
| `speech_id` | `string` | no | The reply the chunks served. |

### `memory.ops`

What memory did for this turn or at hangup: a recall before the reply, a remember after the call, a forget on request.

| field | type | required | meaning |
|---|---|---|---|
| `ops` | `MemoryOp[]` | yes | The operations, in order. |
| `speech_id` | `string` | no | The reply a recall served, when it served one. |

### `prompt.changed`

A block of the prompt was rewritten. The text stays out of the log; its hash and length let two states be compared.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The block that was rewritten, by name. |
| `hash` | `string` | yes | sha256 of the block's new text, hex. |
| `chars` | `integer` | yes | The new text's length in characters. |

### `state.changed`

The app's declared state changed. A tool's result or an outside fact caused it, and the cause says which; the whole state travels so a reader never needs the previous entry.

| field | type | required | meaning |
|---|---|---|---|
| `state` | `object` | yes | The app's state after the change, whole, as the app declares it: its fields are the object's fields. |
| `changed` | `string[]` | yes | The names of the fields that changed. |
| `cause` | `StateCause` | no | What changed it, when the platform knows: the tool whose result moved a field, or the outside fact the app's handler acted on. |

### `tool.call`

The model called a tool. The platform sends this to the app and the app's method runs in the app's own process; tool.result closes it.

| field | type | required | meaning |
|---|---|---|---|
| `call_id` | `string` | yes | The model's id for this call, which the result must repeat. |
| `name` | `string` | yes | The tool's name, as declared in its ToolSpec. |
| `arguments` | `object` | yes | The arguments the model chose, as JSON matching the tool's parameters. |
| `speech_id` | `string` | no | The reply during which the model called it. |

### `tool.result`

The app answered a tool.call. The same shape the app sent as a command, now in the log with its seq.

Data: `ToolResult`, in [shapes.md](shapes.md).

### `tools.changed`

The tools the model can see changed. The app's state moved and each tool's when was recomputed.

| field | type | required | meaning |
|---|---|---|---|
| `visible` | `string[]` | yes | The tools the model can see now, by name. |

