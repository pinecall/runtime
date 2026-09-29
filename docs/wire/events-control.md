# Events: supervision, replay and the agent's own log

What a supervisor did, the replay markers a reader gets, errors, and the entries of an agent's own log (registration, configuration, pong). The index is `events.md`.

### `agent.configured`

The gateway applied an agent.configure. Live calls keep their session; the next call starts with the new config.

| field | type | required | meaning |
|---|---|---|---|
| `changed` | `string[]` | yes | The config fields that changed. |

### `agent.detached`

A socket that held the agent is gone — the process exited, the connection dropped — and the agent's doors are whoever is left holding it. Written to the agent's own log by the gateway, so a console reading the floor sees a process leave as it saw it arrive.

| field | type | required | meaning |
|---|---|---|---|
| `app` | `string` | yes | The socket that left, as agent.registered named it. |
| `env` | `Env` | yes | The world it held the agent in. |
| `left` | `boolean` | yes | True when nobody holds the agent in that world any more; false when another socket still does. |

### `agent.draining`

The answer to agent.drain, written to the agent's own log once the socket's live calls have moved: it takes no new call, and each call it held went to another socket holding the agent or waits, parked, for the next one that registers.

| field | type | required | meaning |
|---|---|---|---|
| `app` | `string` | yes | The socket that is leaving, as agent.registered named it. |
| `env` | `Env` | yes | The world it held the agent in. |
| `handed` | `integer` | yes | Live calls attached to another socket holding the agent. |
| `parked` | `integer` | yes | Live calls waiting for the next socket that registers the agent. |

### `agent.registered`

The gateway accepted an agent.register: this socket now speaks for the agent and answers its routes. Many sockets may hold one agent at once — a new call takes the newest of them, unless the caller names one by its `app` id.

| field | type | required | meaning |
|---|---|---|---|
| `routes` | `Route[]` | yes | The doors the agent now answers. |
| `app` | `string` | yes | This socket, opaque and minted by the gateway: `?app=<id>` on the chat door asks to be served by it. |
| `sdk` | `string` | no | The SDK and version the app runs, as it reported them. |
| `env` | `Env` | no | The world the key that registered it opens: production, or sandbox. Absent on entries written before keys knew where they were, which read as production. |

### `attention.answered`

An ask for a person settled: a supervisor took the line, or the wait ran out and the agent has the caller back.

| field | type | required | meaning |
|---|---|---|---|
| `ok` | `boolean` | yes | True when a supervisor took the line. |
| `by` | `Supervisor | null` | yes | Who took it, or null when nobody did. |
| `error` | `string` | no | Why nobody did, when nobody did. |

### `attention.requested`

The agent asked for a person: the caller is on hold and waits for a supervisor to take the line. What a supervisor's console and phone are notified by.

| field | type | required | meaning |
|---|---|---|---|
| `reason` | `string` | yes | Why a person is wanted, in the app's words. |
| `wait_s` | `number` | yes | How long the caller will wait before the agent has the line back. |

### `callback.requested`

Somebody asked to be called back: a phone caller the overflow agent answered, a web visitor who left a number at the widget, or a caller who asked the agent for one (call.callback). Written into the agent's own log; the tenant's app reads it and places the call.

| field | type | required | meaning |
|---|---|---|---|
| `channel` | `Channel` | yes | The door the public came through: a phone call over SIP, the browser widget over WebRTC, or WhatsApp text. |
| `number` | `string` | yes | The number to call back, E.164. |
| `via` | `"overflow" | "widget" | "agent"` | yes | Who took the request: the overflow agent that answered a phone call the fleet could not, the widget, before any room was made, or the agent itself, on a call. |
| `call` | `string | null` | yes | The call the request was made on, when there was one. Null for a widget visitor who never had a room. |
| `when` | `string` | no | When the caller asked to be called, when the agent said. |
| `note` | `string` | no | What the call back is about, when the agent said. |
| `contact` | `Contact | null` | yes | Who asked, when the app said. |

### `code.claimed`

A code is off the table: a call claimed it, or it expired with none. On the agent's own log, beside the code.issued it closes.

| field | type | required | meaning |
|---|---|---|---|
| `code` | `string` | yes | The code, as code.issued named it. |
| `call` | `string | null` | yes | The call that claimed it; null when it expired unclaimed. |

### `code.issued`

A page asked for a code to show beside the agent's phone number (POST /v1/codes): the four digits a caller keys to bind their call to that page. On the agent's own log, which is the table of codes: a gateway that starts reads back the ones still waiting.

| field | type | required | meaning |
|---|---|---|---|
| `code` | `string` | yes | The four digits. |
| `env` | `Env` | yes | The world the agent answers the phone in. |
| `expires_at` | `number` | yes | When the code stops being one, unix seconds. |
| `log` | `Projection` | yes | The projection the page reads the claimed call through. |

### `credits.exhausted`

The gateway refused a call, a written turn or a register because one of the org's quotas ran out. Written into the agent's own log, which is the org's, before the door says no.

| field | type | required | meaning |
|---|---|---|---|
| `org` | `string` | yes | The org whose quota ran out. |
| `quota` | `"minutes" | "messages" | "agents" | "concurrent_calls" | "memory_facts" | "knowledge_chunks" | "numbers" | "seats" | "llm_tokens"` | yes | Which quota: minutes of call, messages, agents held, calls at once, facts memory keeps, chunks the knowledge bases keep, numbers the box bought for the org, people it seats, tokens its models read and wrote. Every quota a gateway can refuse for is a word here, whether or not that refusal is one an entry is written for: a push and an invitation name no agent, so they are answered at the door and never logged. |
| `used` | `number` | yes | How much the org had consumed when the door refused. |
| `limit` | `integer` | yes | The quota the operator set. |

### `dev.request`

The gateway asks the app process holding the agent to do something only that process can — read a file of the agent's directory, mount its class, run its goldens — on a console's behalf. Sent down the one socket the gateway chose, never stored: a request is a fact about two processes talking, not about the world. The app answers with dev.answer naming the same id.

| field | type | required | meaning |
|---|---|---|---|
| `id` | `string` | yes | The gateway's id for this ask; the dev.answer repeats it. |
| `verb` | `DevVerb` | yes | What a console may ask of the process standing in the agent's directory, relayed by the gateway: a written call to the class mounted there (chat), a simulated caller put on the class it holds, its goldens and a suite of them, its knowledge folder pushed or its golden asked, its memory goldens, the panel it draws beside a conversation (view), a call promoted to a candidate file, the drift of the last two windows, and the reproductions a broken run left on that disk. |
| `data` | `object` | yes | What the console asked, as the verb's own body: the persona and the turns, the goldens ticked, the base name, the call to promote. |

### `error`

Something went wrong. Inside a call it says what failed; outside a call it says which command the gateway refused.

| field | type | required | meaning |
|---|---|---|---|
| `code` | `string` | yes | A stable code a program can match: unknown_command, bad_shape, no_route, tool_timeout, provider_error; and stopped — a member of the org stopped this app (POST /v1/apps/{app}/stop), the socket closes next, and the app exits rather than reconnect. |
| `message` | `string` | yes | What happened, for a person. |
| `command` | `string` | no | The type of the command that failed, when one did. |
| `id` | `string` | no | The app's id for that command, when it sent one. |
| `recoverable` | `boolean` | yes | True when the call goes on; false when this is why it ended. |

### `fleet.full`

The gateway refused to open a call because every worker of the fleet was full. Written into the agent's own log, which is the org's, before the door says no — the caller was offered a call back instead of a room.

| field | type | required | meaning |
|---|---|---|---|
| `channel` | `Channel` | yes | The door the public came through: a phone call over SIP, the browser widget over WebRTC, or WhatsApp text. |
| `workers` | `integer` | yes | How many workers the fleet had at that moment, every one of them full. |
| `active` | `integer` | yes | How many calls those workers were holding between them. |

### `log.caught_up`

The replay is done: everything up to seq has been sent and what follows is live. Never stored; sent to the reader.

| field | type | required | meaning |
|---|---|---|---|
| `seq` | `integer` | yes | The last seq the store held when the reader caught up. |

### `log.gap`

This reader missed a stretch: it reconnected too late for the store, or fell behind and the fanout dropped ephemeral entries. When the platform has a snapshot, it is here so the reader can catch up in one step. Never stored; sent to the reader.

| field | type | required | meaning |
|---|---|---|---|
| `from_seq` | `integer` | yes | The first seq the reader did not get. |
| `to_seq` | `integer` | yes | The last seq the reader did not get. |
| `snapshot` | `State | null` | yes | The state at to_seq, when the platform could compute it. Null when only ephemeral entries were dropped. |

### `message.taken`

A message that was waiting (message.waiting) was answered, on this call — or will never be: `call` is null when it outlived WhatsApp's customer-service window and nothing may be said to it any more.

| field | type | required | meaning |
|---|---|---|---|
| `message_id` | `string` | yes | The message, as message.waiting named it. |
| `call` | `string | null` | yes | The call it was answered on; null when it expired unanswered. |

### `message.waiting`

A message reached one of the org's numbers while no app held the agent it routes to — the process between two deploys, a gateway just restarted. It is kept here, on the agent's own log, and answered the moment a socket holds the agent again; message.taken says when. One older than WhatsApp's customer-service window is never answered.

| field | type | required | meaning |
|---|---|---|---|
| `channel` | `Channel` | yes | Where it was written. |
| `env` | `Env` | yes | The world the number answers in. |
| `number` | `string` | yes | The org's number it was written to, E.164. |
| `phone_number_id` | `string` | yes | The provider's id for that number: what the answer is sent from. |
| `from` | `string` | yes | Who wrote it: the provider's id for the person. |
| `name` | `string | null` | yes | The name the provider shows for them, when it shows one. |
| `message_id` | `string` | yes | The provider's id for the message: what message.taken names. |
| `text` | `string` | yes | What they wrote. |
| `received_at` | `number` | yes | When it arrived, unix seconds. |

### `pong`

The answer to ping. Ephemeral: it proves the socket is alive and says nothing else.

| field | type | required | meaning |
|---|---|---|---|
| `ts` | `number` | yes | The gateway's clock when it answered, unix seconds. |

### `supervisor.ended`

A supervisor hung up the call. call.ended follows with reason supervisor_ended.

| field | type | required | meaning |
|---|---|---|---|
| `by` | `Supervisor` | yes | The human who sent a supervise verb, as the token that let them in names them. |
| `reason` | `string` | no | Why, in their words. |

### `supervisor.released`

The supervisor gave the line back; the agent resumes with the history intact.

| field | type | required | meaning |
|---|---|---|---|
| `by` | `Supervisor` | yes | The human who sent a supervise verb, as the token that let them in names them. |

### `supervisor.said`

A supervisor made the agent say this to the caller.

| field | type | required | meaning |
|---|---|---|---|
| `by` | `Supervisor` | yes | The human who sent a supervise verb, as the token that let them in names them. |
| `text` | `string` | yes | What was said, word for word. |

### `supervisor.took_over`

A supervisor took the line; the agent is quiet until supervisor.released.

| field | type | required | meaning |
|---|---|---|---|
| `by` | `Supervisor` | yes | The human who sent a supervise verb, as the token that let them in names them. |

### `supervisor.transferred`

A supervisor asked for a transfer. call.transferred says how it went.

| field | type | required | meaning |
|---|---|---|---|
| `by` | `Supervisor` | yes | The human who sent a supervise verb, as the token that let them in names them. |
| `to` | `string` | yes | The destination. |
| `mode` | `TransferMode | null` | no | Which of the two the runtime will run, once it has looked at the room. |

### `supervisor.whispered`

A supervisor told the agent something the caller never heard.

| field | type | required | meaning |
|---|---|---|---|
| `by` | `Supervisor` | yes | The human who sent a supervise verb, as the token that let them in names them. |
| `text` | `string` | yes | The instruction the agent received. |

