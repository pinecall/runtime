# The CLI — `pinecall-runtime sessions`

Why the log is read the way it is read: three verbs, one door, and a rule about metrics
that decides most of the rest.

## It reads Postgres, not HTTP

`sessions` opens the database and reads it. It does not call the gateway.

The gateway's `GET /v1/calls/{id}/events` exists and is the door for anything outside the
box: a console, a customer's script, `pinecall/cloud`. This CLI is not outside the box. It
is the operator's tool, run on the machine the runtime runs on, and it is the tool a
person reaches for precisely when the gateway is the thing that is wrong — down, wedged,
refusing a key, or up but writing nothing. A reader that goes through the process under
investigation cannot answer the question being asked of it.

The cost of the choice is honest: `sessions` needs `DATABASE_URL` and the gateway does
not. That is the same setting `migrate` and `doctor` already need, and the same box.

## `tail` polls; it does not subscribe

`log.Fanout` hands a live entry to every reader inside the gateway's process, in memory.
The CLI is another process — usually another machine — and no in-process fanout reaches
it. The alternatives were a socket of its own (a second protocol to keep honest, for one
command) or Postgres `LISTEN/NOTIFY` (a second write path on the hot append, to save a
reader a quarter of a second).

So it polls: `store.since(call, after=cursor)` every 250 ms. Four small indexed queries a
second, on a call that lasts a minute. It is fast enough that a reply appears while the
caller is still speaking, and it has no effect at all on the write path, which is the half
that must never be slowed down to make a reader prettier.

It stops on `call.summary` — the log's last entry, so there is nothing further to follow —
and on Ctrl+C, which leaves through `KeyboardInterrupt` and exits 0 without a traceback. A
person ending a `tail` is not an error.

## Every metric prints, whole

The milestone's rule is that every turn carries every field livekit-agents 1.8 measures,
under livekit's own names. A reader that folded them away would make that rule
unobservable: nobody would notice a field going missing at the source, because nothing
anybody runs would have shown it.

So `show` prints them all. Under every `turn.user` and `turn.agent`, its metrics block,
one field per line, the wire's name verbatim; nested objects become `metadata.model_name`
rather than being flattened into an invented word. Every `metrics.<block>` entry prints
the same way, whole. Nothing is hidden behind a flag, and no name is shortened for width.

The order is the schema's, not the storage's: jsonb hands keys back in its own order, and
a transcript that reshuffles itself depending on which store it came from is not a
transcript. `declared_order()` reads it off the protocol model.

Under all of it, one table: the medians of `e2e_latency`, `llm_node_ttft`,
`tts_node_ttfb`, `transcription_delay` and `end_of_turn_delay`, computed from the turns
themselves. Median and not mean, because one interrupted turn moves an average and what a
person is asking is what a normal turn felt like. A measure no turn carried has no row —
never a zero, which would read as an instant answer.

## Rendering is a pure function

`cli/sessions/render.py` turns a list of `Entry` into a list of strings — the transcript and the
table under it — off the medians `log/latencies.py` reads, which `pinecall eval` reads too. Neither
opens a connection, and neither prints. That is what
lets the acceptance test be a unit test over `protocol/fixtures/call-log-golden.json`
asserting that every metric key of every turn and of every `metrics.*` entry appears in
the output by name — the criterion, checked directly rather than through a screenshot.

## PII is masked where it is written, not where it is read

`log/pii.py` masks on the way in, by what the agent declared, and the tenant projection
masks again at the sink, where the token says who is reading. `sessions` prints the entry
as it is stored. It is the operator's own tool on the operator's own box: masking there
would hide from the one reader who is entitled to everything, and would say nothing about
what a tenant can see.

## One question the Store does not answer

`Store` is about one log — that is what every writer needs and what keeps `log/` portable.
"Which calls exist" and "which call is still live" are about all of them, and only an
operator ever asks. They live in `cli/sessions/source.py` as two statements against
`call_log_head`, rather than widening a Protocol every future backend would have to
implement. `Source` is the one object the verbs hold; `Calls` is the Protocol they are
typed against, so the tests hand in a database of their own.
