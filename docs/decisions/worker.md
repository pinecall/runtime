# The worker: the process, the job, the router, the session

Process 2 of two. It joins a fleet, is given one job per call, and answers it. It has no
database, no cache and no queue: everything it knows it asked the gateway over HTTP, and
`tests/test_isolation.py` fails the day a module under `worker/` names
`pinecall.gateway`.

## The files

| file | the one idea |
|---|---|
| `main.py` | the process: one `AgentServer`, one `rtc_session`, one fleet name |
| `entry.py` | one job, from the room to the sealed log |
| `router.py` | who this job is for: the dispatch, the number dialled, the default |
| `client.py` | the worker's only door to the platform |
| `kit.py` | `providers/` as the process holds it: one agent in, its vendors out |
| `session.py` | the `AgentSession` for this call, and how it takes its turns |
| `hearing.py` | what the ears expect: the declared words, and the names the state holds |
| `state.py` | the prompt's regions on a live agent |
| `clock.py` | today's date, as a pair the model reads as its own |

## The fleet name is never empty, and that half is ours

`AgentServer.rtc_session` refuses a second entrypoint by itself (`agents/worker.py:502`), so
"one rtc_session" is the library's rule and we only have to not ask for two. The other half is
not: `agent_name` resolves `LIVEKIT_AGENT_NAME_OVERRIDE` → the argument → `LIVEKIT_AGENT_NAME`
→ `""` (`worker.py:512-520`), and `""` means **implicit dispatch to every room in the
deployment** (`worker.py:219`) — somebody else's call, answered by us. Nothing in livekit
refuses it, so `a_server` does: an empty fleet is a `ValueError` before the process starts.

## The entrypoint is a name, and the Worker is built where the job runs

livekit runs each call in a process of its own — `spawn` on macOS, `forkserver` on Linux
(`worker.py:259`) — and hands that process the entrypoint **by name**: the function is pickled as
module plus qualname, imported again on the other side. ms-3's integration card found this the
only way it can be found, by dispatching a job: `a_server` used to register a closure over the
`Worker`, and the first job died in `ForkingPickler` with *Can't get local object
'a_server.<locals>.job'*. The console (`pinecall-runtime worker talk`) never showed it, because livekit runs the
console's jobs on a thread (`cli/_legacy.py:1491`).

So `main.job` is a module-level function and `main.a_worker(load_settings())` builds the Worker
**in the job's own process**, from the environment it inherited. That is also why the gateway's
URL and the default agent are `PINECALL_GATEWAY_URL` and `PINECALL_AGENT` — fields of `Settings`
— and not `--gateway` / `--agent` flags: a flag parsed by the parent never reaches the child, an
environment variable does. An operator sets both in the environment of the worker they start.
`test_the_entrypoint_survives_the_trip_to_a_job_process` pickles the entrypoint and reads it back.

## The load a worker REPORTS is the gate, and dev mode does not lift it

Two gates, and the worker only knows about one. Its own: `_is_available()` compares its load
against `load_threshold`, whose dev default is `math.inf` (`agents/worker.py:148,1336-1349`) — in
`dev` the worker refuses nothing. livekit-server's: the job goes only to a worker whose **reported**
load is under `target_load`, `0.7` by default — `affinity += max(0, targetLoad - w.Load())` in
`pkg/service/agentservice.go` `JobRequestAffinity`, `const DefaultTargetLoad = 0.7` in
`pkg/agent/config.go` (livekit-server 1.13.6). Affinity `0` and psrpc answers the dispatch with
`no servers available (received 1 responses)`.

What a worker reports, unless it is given a `load_fnc`, is `_DefaultLoadCalc`: the **whole
machine's** CPU average over 2.5s (`worker.py:83-113`), put on the wire every 2.5s
(`_update_worker_status`, `worker.py:1509`). So a laptop that is also building something is over
`0.7`, and every dispatch is refused while the worker sits registered and silent — no
`received job request`, no error, nothing to read. That is exactly the regression of 2026-09-07,
first blamed on `Settings` because the run that worked had `LIVEKIT_*` exported; the environment
had nothing to do with it, the machine was simply busier in the run that failed. Measured, on one
laptop, nothing exported, changing only the reported load: `0.95` → `no servers available
(received 1 responses)`, `0.69` → `assigned job to worker`.

**A worker that is registered and never assigned is almost always reporting a load of 0.7 or
more — the box's CPU, not anything of livekit's.** That is the sentence to read first the next
time a fleet goes quiet.

So `worker/load.py` holds both halves of what a worker reports. `reports_no_load` is `dev`'s:
`a_server(settings, gated_by_machine_load=False)` hands it to livekit's own `load_fnc` parameter,
and it says out loud what dev mode already means. `MachineLoad` is `start`'s: it keeps livekit's
own calculator — read off the public option that carries it, the default of the `load_fnc` field of
`WorkerOptions` (`worker.py:188`), never off the private class behind it — because on a box that gate IS the backpressure — a machine at 0.7 should stop
being sent calls — and it makes the silence impossible by logging one line per **crossing**:
`WARNING` naming the number the first tick at or over `REFUSED_AT`, `INFO` when it falls back
under. Nothing of ours polls: livekit calls `load_fnc` on its own tick, every 2.5s
(`worker.py:810`) and again before each availability check (`worker.py:1313-1321`). The crossing
is remembered on the instance, one per `AgentServer`, never a module-level flag.

## Prewarm: livekit's models are livekit's, the process's own imports are ours

`AgentServer` appends `livekit.agents.inference._warmup` to the forkserver's preload list
(`worker.py:747-759`). That module is three lines — `import livekit.local_inference as _li`,
`_li.init_vad()`, `_li.init_eot()` (`inference/_warmup.py:7-10`) — so the native VAD and the
end-of-turn weights are resident in the forkserver before any job exists and every forked job
inherits the pages by COW. Those are exactly the two models a call cannot wait for, and they are
the two the session builds by itself (`agent_session.py:541-542,606-607`). **We register no
prewarm of our own for either** — ms-3 wrote one and tk-ffc7ea deleted it, correctly.

What `setup_fnc` carries now is a different thing in kind: not a model, but OUR OWN MODULES. The
vendor tables are filled by importing the package (`providers/registry.py::_read_the_package`),
which is lazy, so the FIRST `pipeline_for` of a job process imports four livekit plugin packages
with the caller already seated in the room — measured below. livekit hands an idle process its own
hook for exactly that (`AgentServer(setup_fnc=…)`, handed to the pool as `initialize_process_fnc`
at `worker.py:620`), so `worker/main.py::warmed` reads the three tables there and no call pays for
an import again. Nothing is written into `proc.userdata`: the imported modules hold themselves.

The preload is conditional on `self._mp_ctx_str == "forkserver"` (`worker.py:747`), and the
context defaults to `forkserver` on Linux and `spawn` everywhere else (`worker.py:259-260`). Our
box is Linux and gets the COW preload; a laptop running `pinecall-runtime worker talk` spawns, and the native
singleton loads lazily on the first call of each job process. We do not override the context —
`spawn` is what livekit chose for macOS, and a laptop is not where latency is measured.
`initialize_process_timeout` is `10.0` seconds (`worker.py:215`): the budget `warmed` spends about
a third of a second of.

### The console has no job process, so it warms before livekit is handed the process

`setup_fnc` was written for the two verbs that dispatch, and it broke the third. `dev` and `start`
run a job in a PROCESS of its own, where the setup hook is the main thread and importing a plugin
is fine. `talk` does not: livekit's console sets `JobExecutorType.THREAD` on the server it is given
(`cli/cli.py:227`, and the legacy console at `cli/_legacy.py:1491`), so the hook is called from
`job_thread_runner` — `ipc/job_proc_lazy_main.py:478` → `ipc/proc_client.py:46` →
`ipc/job_proc_lazy_main.py:225` → `warmed`. A vendor plugin package registers itself as it is
imported, and `agents/plugin.py:31-33` refuses that anywhere but the main thread:
`RuntimeError: Plugins must be registered on the main thread`, and `worker talk` died at startup.

Three shapes were on the table. **Reading the tables at IMPORT time of the worker's entry module**
would warm both modes, but it puts one to four seconds of vendor imports behind every
`pinecall-runtime` verb, `doctor` and `keys` included, because the CLI imports its groups to build
the parser. **A no-op when `threading.current_thread() is not threading.main_thread()`** is one
line and silently hands the console a cold first call — the very second this hook exists to save.
**What is written**: the console reads the tables on the CLI's own main thread, in
`cli/worker.py::hand_over`, on the last line that is certainly still ours before `run_app` takes
the process over (`THREADED_VERBS`). `warmed` is unchanged and still runs for every verb; reading
a table is idempotent (`providers/registry.py::_read_the_package`), so in the console it finds the
three tables read and does nothing. `dev` and `start` keep paying the import inside an idle job
process exactly as before — the CLI's main thread does nothing extra for them, so the measured gap
below is untouched. `tests/cli/test_worker.py` pins both halves: which verbs warm before the
hand-over, and — in a fresh interpreter, because this suite's own session fixture has already read
the tables here — the whole console setup path driven from a thread named `job_thread_runner`,
which raises without the fix.

The same rule was met from the other side before, in `tests/conftest.py`'s session fixture: a
TestClient serves in a portal thread.

## The gap between the job arriving and the pipeline being live

The card that opened this said the gap was three HTTP round trips and asked for a per-fleet cache
with a short TTL. **Measured, that premise was wrong**, so it is written down here with the
numbers rather than quietly dropped. Six dispatched calls into a running gateway on the same box,
phase by phase, seconds:

| phase | before |
|---|---|
| `ctx.connect()` | 1.05 · 1.55 · 1.67 · 1.04 · 1.05 |
| the SIP seat | 0.000 (a dispatch names the agent, so nothing is waited for) |
| `GET /v1/routes` | 0.037 · 0.085 · 0.058 · 0.038 · 0.046 |
| `GET /v1/agents/{slug}/config` | 0.004 – 0.008 |
| `POST /v1/calls` | 0.009 – 0.033 |
| **`session.a_session`** | **1.348 · 4.181 · 2.338 · 1.701 · 2.219** |
| `bridge.opened` | 0.011 – 0.085 |
| `live.start` | 0.119 – 0.657 |

The three round trips are 0.05 – 0.13 s together. And caching them per fleet buys nothing at all:
livekit gives every job its OWN process — the pool hands out a warmed one and consumes it
(`ipc/proc_pool.py:164,251`) — so a process-level cache with a TTL is a cache of one.

The gap is `session.a_session`, and inside it the first `pipeline_for`, which imports the vendor
plugin packages: 0.455 s in a warm shell, 1.3 – 4.2 s in a cold job process on a loaded machine,
0.005 s every time after. So two changes, in that order of size:

1. `setup_fnc` reads the vendor tables while the process is still idle (above). On `start` the
   pool keeps warmed processes ahead of demand, so the import is off the caller's path entirely;
   on `dev` there are none by default (`worker.py:207-208`) and it moves from inside the call to
   the process initialisation the job waits on — still off `answer()`, and still the honest place.
2. `GET /v1/routes` is issued CONCURRENTLY with `ctx.connect()`: the routes table belongs to the
   fleet, not to this call, so nothing about it has to wait for a room. `GET /config` stays after
   the seat is read, because which agent it asks for depends on what was dialled — and it is 5 ms.

Six more calls on the same box, quiet, the two versions back to back — `answer()` entry to
`live.start()` return: **before 0.62 · 0.64 · 0.72 · 0.64 · 0.64 · 0.66 s; after 0.35 · 0.30 ·
0.31 · 0.39 · 0.32 · 0.32 s.** Halved. Process initialisation grows by the same third of a second
it lost (0.65 – 0.78 → 0.88 – 1.12), which is the point: that time is now spent where nobody is
listening. The earlier six, taken while three other agents were hammering the machine, ran 2.74 –
6.24 s before; the same load is what turns a 0.45 s import into a 4.2 s one.

`entry.py` logs the number on every call — `the pipeline is live 0.32s after the job arrived` — so
this never has to be re-measured by hand. **Re-measured after the console fix** (tk-048b43, six
dispatches to a fleet of its own on the same laptop, a gateway and a browser alongside):
**0.34 · 0.38 · 0.40 · 0.32 · 0.32 · 0.41 s**, process initialisation 1.09 – 1.34 s. The same
band as the 0.30 – 0.39 above, on a busier machine: warming the console's tables changed nothing
for `dev`, which is the point of putting it in the CLI rather than in `warmed`.

## What the caller is heard with, and what the caller hears

Four knobs livekit's own examples set and we did not (`voice_agents/basic_agent.py:73-112`), all
of them on the spoken session and none on a written one.

**`tts_text_transforms`.** livekit already filters markdown and emoji out of every reply it speaks
(`agent_session.py:351`) — the prompt ASKS for no asterisks, the library GUARANTEES it. We name
the two filters anyway, because the parameter REPLACES the default list rather than extending it,
and the tenant's own map goes after them: `says = { "Vidal": "bidál" }` becomes
`text_transforms.replace(…)`, applied to text the filters already cleaned. The log keeps what the
model wrote; only the voice is handed the spoken form.

**`stt_context_options`.** `hears = ["Clínica Norte", "doctora Vidal"]` is the tenant naming the
words a general model mishears. livekit's own example turns `keyterm_detection` on as well, which
is an LLM call every `turn_interval` (`keyterm_detection.py:88-96`) to GUESS what the agent could
have declared: we leave it off. Where the words go depends on the vendor's door. Deepgram Flux
advertises the keyterms capability (`stt_v2.py:124`), so the session hands them over itself and
merges them with the socket's (`stt/stt.py:286`). Soniox advertises none (`soniox/stt.py:185-191`)
and its door is `context.terms` (`soniox/stt.py:74-84,116`), filled in `providers/stt/soniox.py`
from the same declaration — and handing keyterms to ears that take none only logs a warning per
call (`stt/stt.py:293-298`), which is why the session's option is gated on the capability.

And one extension that is ours, not the examples': **the state is a source of keyterms**. The
class already knows who it is talking to, so `worker/hearing.py` adds to `hears`, whenever the
state moves, the values of the state that read like names — a string with a letter in it, at most
four words and forty characters, one level into an object, deduped, capped at fifty. A clinic
cannot declare its patients; the tool that just identified Ana wrote her name, and the next turn
is where the caller says it again. `AgentSession.update_options(keyterms=…)` replaces the
session's own set and leaves anything detected alone (`agent_session.py:1366`).

The known limit: this reaches a vendor with the generic door and NOT Soniox, whose stream reads
`self._stt._params.context` at every (re)connect (`soniox/stt.py:261-278`) with no update door of
its own. Declared `hears` still reaches Soniox; the per-turn additions do not, silently, rather
than warning once a call. Opening that door is a card of its own.

**`resume_false_interruption`.** convo hand-rolled a recovery for the agent being cut off by a
noise: it remembered the sentence, waited, and said it again. livekit 1.8 does it in the session —
resuming is the DEFAULT (`turn.py:195`), so it is not written again here; the number is, because
the library waits 2.0 s and its own example waits 1.0 (`basic_agent.py:93`), and two seconds of
silence on a phone line is a caller wondering whether the call dropped. The test pins the RESOLVED
option, not our dict, so a future default flip cannot take the behaviour away quietly.

**`ctx.log_context_fields`.** One assignment at the top of `answer`, and every livekit log line of
the process names the room. A box running forty calls at once had no other way to read its own log.

## Preemptive generation: kept, out loud

1.8 turned it **on** by default (`voice/turn.py:223`); it was opt-in in 1.7.x. We keep it for a
spoken call and say so in the code rather than inheriting it silently: the LLM runs before the
turn is confirmed, up to 3 attempts, for utterances under 10 s, and the caller hears the half
second it saves. What it costs is a discarded attempt that still emits `LLMMetrics` with
`cancelled=True` (`agents/llm/llm.py:401,446`) — the tokens were spent, so the bridge will see
`metrics.llm` entries with no turn behind them and `prices.py` counts them. On the default model
that is a Haiku prompt against a caller waiting on a phone line, and the trade is worth it.

`preemptive_tts` stays off: speaking before the turn is confirmed is not a latency win, it is the
agent talking over somebody. A **written** channel disables preemption outright — a written turn
arrives whole, so there is nothing to race and nothing to throw away.

The end-of-turn detector is the one thing `session.py` names: `turn_handling` carries
`inference.TurnDetector(version="v1-mini")`. Left unset, the library resolves the version by
reading whether it is hosted or in dev mode and picks the cloud `v1` if so
(`inference/eot/detector.py:55-59`), which would put a caller's transcript on somebody's gateway
because of an environment variable. The VAD is the opposite: `vad=` is left undeclared on a spoken
call, so the session builds livekit's own native one at `min_silence 0.25`
(`agent_session.py:606-607`, `inference/vad.py:64`) — the number we would have asked for. A written
call passes `vad=None`, so none is built at all.

`Turn.endpointing_ms` is **not** copied into livekit's endpointing. It is already the ASR's own
endpointing — `providers/` hands it to the STT, where a test reads it off soniox's parameters — and
setting livekit's `min_delay` from the same number would make one knob wait twice. What the session
gets from the agent's `Turn` is `min_interruption_words`, which has no other consumer; everything
else is livekit's default, and with a streaming turn detector that is its tighter one
(`voice/turn.py:142`).

**Interruptions are judged by the local VAD, said out loud.** `InterruptionOptions.mode` left
absent means "auto", and auto picks the **adaptive** detector whenever the process is in dev mode
or hosted (`agent_activity.py:4842-4849`) — `inference.AdaptiveInterruptionDetector`, a WebSocket
to `agent-gateway.livekit.cloud/v1/bargein` carrying the caller's audio. ms-3's integration card
found it on the first job `worker dev` ever ran: a 401 every two seconds for the length of the
call, and an audio upload a self-hosted box must never make. `session.py` sets `"mode": "vad"`,
livekit's own local strategy, so `dev`, `start` and the console behave the same. It is the same
decision as the turn detector's `v1-mini`: nothing about a caller leaves the box because of the
mode a process happens to run in.

`max_tool_steps` is left at livekit's own `3`. The text session sets 8 because a reader waits
differently than a caller; three rounds of tools on a phone line is already several seconds of
silence, and a number copied here would be a second place to change it.

## The router reads the job and the room, and the door decides the channel

Three sources, in order of certainty: the dispatch metadata (`{"agent": …}` — an outbound call
always arrives this way, because we created the dispatch), then `sip.trunkPhoneNumber`, the
number that was dialled, which is the door and therefore the route, then the default agent the
process was started with, which is how `pinecall-runtime worker talk` reaches one agent on a laptop with no
routes table. There is no fourth: the environment is read in `_settings.py` and nowhere else, so
"the env" of the original sketch is a flag the CLI passes in.

The first source is the job; the second is **not**. The dialled number is on the caller's SIP seat
in the room and never on `job.participant`, which livekit fills for a publisher job and leaves
empty for the room job a phone call is — see `docs/decisions/sip.md`. `arrival_of` therefore takes
the job *and* the room, and `answer` joins the room before it resolves anything.

An agent answers **only** on the channel the call arrived through. Falling back to another of its
doors would put "web" in the log of a real phone call, and a log that lies is worse than a refusal
that names the number nobody answers.

The call's id is the room's name. A reader of the log can then find the room and the room can find
the log, with nothing minted in between and nothing to keep in step.

## The date is a tool pair, not a system message

`convert_mid_conversation_instructions` is now the shared helper every JSON-object provider calls
(`_provider_format/utils.py:49`): the first system message is the preamble and **every later one
is rewritten with `role="user"`**. A date appended as a system message therefore arrives in the
model's transcript as something the caller said. So `clock.py` seeds a `FunctionCall` and its
`FunctionCallOutput` under one id, which the formatter groups and sends as themselves.
`reply_required=False`, because nothing was asked. The pair goes in with
`exclude_invalid_function_calls=False`: the clock is nobody's tool, and livekit filters out a call
the agent does not hold (`voice/agent.py:255`).

## The prompt's regions, and what 1.8 writes into the history

`Regions` holds the static region and the view. The static region **is** livekit's
`instructions`, the one pinned item at index 0 (`generation.py:1225`) that Anthropic's cache
breakpoint lands on; the view is rendered from state every turn and belongs after the history, at
the end of the request, which is the bridge's seam. Rewriting the prefix with the same bytes still
pays a cache write, so the same text twice is not an update at all.

New in 1.8, and what `state.py` had to expect: `update_instructions` and `update_tools` also
insert an `llm.AgentConfigUpdate` into the agent's and the session's chat context
(`agent_activity.py:604,631`). It is a member of the `ChatItem` union, so it is in `history` and
in anything we serialise, and `_ChatItemGroup.add` has no branch for it
(`_provider_format/utils.py:157`) — it is recorded and never sent. Two tests pin exactly that: the
system blocks the Anthropic formatter produces are byte-identical across a tools update, and the
names in the config update never appear in the request.

## What the worker asks the gateway for, and what it does not

`client.py` is four reads and three writes: the routes, one agent's config, the call opened, an
entry appended, the log sealed. The two processes exchange the **domain object itself**, adapted
by pydantic (`TypeAdapter(AgentConfig)`), and that is deliberate: the one wire-to-domain
conversion in the tree is `api/agents/declaration.py`, at the app's edge, where the SDK's schema
meets ours. An internal hop between two processes of the same distribution is not a second
protocol and must not grow a second converter.

`Bridge` is a Protocol here, not a class: the worker owns the job's life and the bridge owns the
log. The shutdown callback tells the bridge the job is ending — it writes `call.ended` and
`call.summary` with livekit's own usage rows and the cost — and then seals the log, in that order,
because sealing first would close the log under the entries that say how the call ended.

## What the bridge card added

The gateway side of the five doors `client.py` knocks on landed with the bridge (`api/agents/
endpoints.py` for the two reads, `api/calls/endpoints.py` for the three writes, `gateway/tools.py`
for the tool round trip), and `tests/gateway/test_worker_doors.py` drives this very client against
the real ASGI app. `Bridging` is `session/voice/voice.py`'s `a_bridge` — see
[voice-bridge.md](voice-bridge.md). `main.run()` still takes the process's pieces as arguments, so
the CLI verb that starts a worker — flags, signals, fleet name — remains the worker CLI card's.

`min_words` is the one turn-handling default that is ours: livekit's is 0 and
`session/voice/barge_in.py` says two, for the reasons written there.
