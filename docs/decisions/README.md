# Decisions

Why each module is the way it is. The code keeps the what: a file gets a one-line docstring and
nothing more, so the argument lives here, one page per module, and grows with the code. Add a
row when a module lands; append, never reorder. The wire's own decision is in the protocol
repository; the framework's pages are in the agents repository.

| module | the why |
|---|---|
| the infra stack | [infra.md](infra.md) |
| the types: the contracts, why dataclasses, the wire agreement | [types.md](types.md) |
| the log: the entry, the store contract, seq and seal | [log.md](log.md) |
| the api: the app socket, the registry, the keys, the process memory | [api.md](api.md) |
| the CLI: reading the log back — sessions list, show, tail | [cli.md](cli.md) |
| the agent class, as the runtime receives it: the author rule, docstrings | [agent.md](agent.md) |
| the confirmation gate, and why it was removed for now: the wire kept it, the runtime did not | [confirm.md](confirm.md) |
| the runtime bridge: one instance per call, send only when the text changed | [bridge.md](bridge.md) |
| the world: visibility, onEvent, say and reply, the room and the history | [agent-events.md](agent-events.md) |
| the providers: why the livekit plugins are the adapters and LLMMetrics is the measurement | [providers.md](providers.md) |
| the prompt's three regions: why the view is last, where the code cuts | [prompt-regions.md](prompt-regions.md) |
| the text session: livekit's AgentSession runs the loop, the log is written from its path | [text-session.md](text-session.md) |
| auth: one token for text and voice, on livekit's AccessToken | [auth.md](auth.md) |
| livekit-agents 1.8.0: the verdicts at a glance, and the chapter each one lives in | [livekit-1.8.md](livekit-1.8.md) |
| the worker: the process, the job, the router, the session it builds | [worker.md](worker.md) |
| the voice bridge: session events to entries, commands to session actions, the read-back, the barge-in stoplist | [voice-bridge.md](voice-bridge.md) |
| the room: its facts into the log, the verbs as livekit-api calls, the DataChannel projected in the worker | [room.md](room.md) |
| the routes: the two tables, which wins, the table, the doors, the CLI | [routes.md](routes.md) |
| the box: one machine, the four fences on 5060, the trunk created once | [box.md](box.md) |
| the caller's SIP leg and the cold transfer: the bounded lookup, the outcome, why warm waits | [sip.md](sip.md) |
| evals: the four rings, and the chapter each one lives in | [evals.md](evals.md) |
| the API keys: printed once, revoked never deleted, and the three keys a box holds | [keys.md](keys.md) |
| supervise: six verbs, one command, two doors, the whisper's two halves, why a takeover goes deaf | [supervise.md](supervise.md) |
| the pipeline door: the one hop an override takes, the blank that is refused, the medians | [pipeline.md](pipeline.md) |
| the settings: the two .env paths, why a real variable wins, the generated example | [settings.md](settings.md) |
| livekit's official examples: every knob they set, and ours-too / not-yet / not-for-us | [livekit-examples.md](livekit-examples.md) |
| tools and the state: why `when` is the primitive, why `stage` is sugar and what it types | [agent-model.md](agent-model.md) |
| livekit 1.8, invariants 1-6: the chat context on its way to the model | [livekit-context.md](livekit-context.md) |
| livekit 1.8, invariants 7-12, 15-17, 20: the session, the server, the plugin defaults | [livekit-session.md](livekit-session.md) |
| livekit 1.8, invariant 19: where a word timing comes from | [livekit-words.md](livekit-words.md) |
| many app sockets on one agent: newest wins, a call sticks to the socket that took it, `?app=` | [dispatch.md](dispatch.md) |
| ring 3: the replay door, the four statuses, and the fixtures | [evals-ring-3.md](evals-ring-3.md) |
| rings 1 and 2: what livekit 1.8's own testing framework carries, and where a suite becomes a product | [evals-rings-1-and-2.md](evals-rings-1-and-2.md) |
| the judges: livekit's `Judge` as the library, the policy that answers for free, the case, the matrix | [evals-judges.md](evals-judges.md) |
| one consent rule: the order in `types/`, and what the two copies of it disagreed about | [evals-consent.md](evals-consent.md) |
| the eval runner: goldens through the connected app, the settle rule, the graphs a golden asks for | [eval-runner.md](eval-runner.md) |
| ring 4: `call.score` as the terminal entry, the four verdicts, the ceiling, the seqs a judgment cites | [scoring.md](scoring.md) |
| the Evals screen's doors: the delta that names rows, `panel`, the agent-narrowed runs door | [evals-screen.md](evals-screen.md) |
| the token door: LiveKit's endpoint, `agentName` and the dispatch in the token, where "once" is kept | [tokens.md](tokens.md) |
| the orgs: the tenant as a row, the key as the tenant, whose log is whose | [orgs.md](orgs.md) |
| the provider keys: managed is the absence of a row, BYOK is one row, and the one door that answers with a key | [provider-keys.md](provider-keys.md) |
| the third door: why Meta's Cloud API directly, the webhook contract, one thread per contact, the two hours that honour the twenty-four | [whatsapp.md](whatsapp.md) |
