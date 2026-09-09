# The domain

Why the contracts are the way they are. The code is `src/pinecall/types/`, one
module per idea: `agent`, `call`, `tool`, `route`, `token`, and the two they share, `channel`
and `refused`. Nothing here was copied from Pinecall v1 or convo; the vocabulary is the v2
design's, and a field survived only when the runtime reads it.

## Frozen dataclasses, judged once

An agent is an object; types are contracts. A declaration is judged at construction, in
`__post_init__`, and refused with `DeclarationRefused`, which is a `PinecallError` and a
`ValueError` both: the runtime catches it by its own name, and Python by the name a
constructor refusing its arguments has always had. It lives in `domain/refused.py` rather than
`_exceptions.py` because the domain is the only place a declaration is judged; folding it into
the root list is one line, if the root list is where Bernardo wants every name.

Every contract is frozen. Nothing about a declaration changes during a call; what changes is
in the log, with a seq. `CallContext` is frozen for the same reason with more force: it is
what is true before the first word, and the session must never learn something from it that
the log does not also carry.

## Not pydantic, and not the generated models

The wire's models are pydantic and generated from the schema. The domain imports none of
them. The wire is what an app may *say*: its `AgentConfig` is all-optional because a
`configure` changes one thing. The domain is what the runtime *knows*: its `AgentConfig` is
resolved and its rules are the runtime's rules, which the schema cannot express (a tool
irreversible without a read-back, a route with no door, a pii field naming an argument the
tool does not have). Mirroring costs a conversion at the boundary, which is the gateway's
registry's job, and buys a domain no schema change can reshape by accident.

What holds the two together is a test, not a shared import: every field the wire shape carries
has a domain field of the same name (`tests/domain/test_wire_agreement.py`), and the closed
sets (`Channel`, `Direction`) are equal, set for set. The protocol amendment adds
`state_fields` and `events` to the wire's `AgentConfig`; the domain already carries both under
those names, so the same test proves the agreement the day the schema lands.

## ToolSpec: three side effects, one template, and no `when`

The domain says `side_effect: read | write | irreversible` and `confirm: str | None`, the
template the agent reads back. Three, because the policy is deterministic and needs three
answers: a read runs; a write runs and can be undone; an irreversible tool runs only after the
caller's yes. The wire carried two booleans when this page was written, and the open question
was who composes the read-back. The registry card answered it — the tenant does — and changed
the schema to the domain's own two shapes, so the conversion is by name with no projection:
`docs/decisions/api.md`, "The wire learned the domain's ToolSpec".

A template gates any tool that has one: `requires_confirmation` is "there is a read-back",
and an irreversible tool without one is refused. `pii` names arguments, so it is checked
against the schema's `properties`: a masked field that does not exist masks nothing.
`preview` is how many items of a list result the model sees; the app keeps them all.
`result_summary` is the one-line template for the console and memory. `timeout_s` defaults to
thirty seconds because the app's method runs in the app's own process and the model is
waiting on the line.

`when` is not here on purpose. Visibility is a predicate over the app's state, evaluated
where the state lives; the app sends `tools.set` with the subset that passes. The platform
never runs a function of the tenant's.

## Route: a fleet, a door, an agent

A number is a route, never an agent. A route is `fleet + (channel, number) → agent`: the
fleet names which worker pool carries the call (the worker registers under one `agent_name`),
the door is what the public dials or opens, the agent is the slug that answers. `door` is the
pair the registry keeps unique: two routes at the same door are the same door whatever the
fleet or the label. Phone and WhatsApp need a number in E.164 form because that is what SIP
and the WhatsApp API hand over; the web widget has no number, and a route that gives it one
is refused rather than ignored.

## CallContext: what is true before the first word

`channel`, `direction`, `caller` (E.164 or the visitor id), `route`, `contact`, `metadata`
(sealed by the platform, the app's own), and `today`. The date is a field, not a call to the
wall clock: the door that opens the call gives it — the worker's router for a voice call, the
chat, WhatsApp and eval doors for a text one (a golden may pin it, `today`) — so a test can say
what day it is and the prompt's clock tool (`pinecall/clock.py`) answers from the context. The card's list did not name `call` (the id) or
`direction`; both are here because a context that cannot say which call it is cannot be
appended to a log, and the first entry of a log says which way the call went. A context whose
route is not on its own channel is refused: a web call through a phone door is a bug, not a
configuration.

## Token scopes, as data

Five scopes, a closed set: `talk`, `chat`, `observe`, `supervise`, `participate`. Each is a
`Grant`, eight yes-or-no fields the gateway checks and reasons no further: whether it opens a
session, with audio, whether it reads the log, sends the verbs, is bound to its own call, is
single-use, and how long it lives. `talk` and `chat` open one session, once, and die in a
minute, because the tenant's server mints them per visit and the browser never mints one;
`chat` is `talk` with the audio off both ways. `observe` reads and does nothing else.
`supervise` is the only scope that sends verbs. `participate` reads its own call and no other.
A scope nobody declared grants nothing, by refusal rather than by an empty grant.

`GRANTS` is the ONE scope table. `auth/scopes.PROJECTION_OF` used to be a second one, listing the
same five scopes against a projection each; it is now derived from the grant — a scope that reads
the log past its own call is reading the tenant's, everything else reads the public projection —
so a sixth scope is declared in one place and the two halves cannot drift apart. The derivation
lives in `auth/scopes.py` because that is the module allowed to spell the projections; the domain
holds booleans and never the words.

## Channels

`phone`, `web`, `whatsapp`, and `inbound` / `outbound`, mirrored from the wire as literals
and tested equal to it. `CHANNELS_WITH_A_NUMBER` is the one fact about them the contracts
need: a number answers on the phone and on WhatsApp, and nowhere else.
