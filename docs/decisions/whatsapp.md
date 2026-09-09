# whatsapp — the third door, and why it talks to Meta itself

The public talks to the agent by web, WhatsApp and phone. This is the second of the three: a
webhook on the gateway, one text call per contact, and the agent's turns back out through Meta's
Graph API on the org's own token.

## Why Meta's Cloud API directly, and not LiveKit's connector

LiveKit ships a WhatsApp connector. Its own telephony documentation says: *"Connectors are
available in LiveKit Cloud only. Self-hosted LiveKit servers are not supported."* Building the
third door on it would mean the same image does NOT run on a customer's box, which is the one
promise this repo makes about itself (`CLAUDE.md` → "Open and private"). So the door is ours: an
HMAC-verified webhook, and one `POST /{phone_number_id}/messages` per answer.

The cost is a webhook contract we own. The gain is that WhatsApp works on a laptop with
`docker compose up`, on our box, and on a customer's, with no account anywhere but Meta.

## The webhook contract

Two routes, both under `/v1/whatsapp/webhook`, both with **no API key**: Meta is the caller and
the signature is the authentication.

| | |
|---|---|
| `GET` | Meta's subscription handshake. `hub.mode=subscribe`, `hub.verify_token`, `hub.challenge`. The challenge comes back as `text/plain` when the token is `PINECALL_WHATSAPP_VERIFY_TOKEN`, 403 otherwise. |
| `POST` | Every message Meta delivers. The raw body is verified against `PINECALL_WHATSAPP_APP_SECRET`, then read, then queued. `{"received": n}`, 200. |

**The signature is over the RAW bytes.** `X-Hub-Signature-256: sha256=<hex>`, HMAC-SHA256 of the
body under the App Secret, compared with `hmac.compare_digest` (`whatsapp/signing.py`). JSON
round-tripped through Python is not the bytes Meta hashed — whitespace and key order both differ —
so the endpoint reads `await request.body()` before anything parses it, and the tests build and
sign their bodies the same way.

**200 always, once the signature has passed.** A body this door does not understand — a delivery
receipt, a template status, a shape Meta added last week — is one warning line and a 200. Meta
disables a webhook that keeps answering 4xx, and losing the subscription is worse than losing a
message we were never going to read. The two exceptions are before the body is read at all: 403
for a bad signature, and 503 when the box has no `PINECALL_WHATSAPP_APP_SECRET` at all (the same
shape as the vault's `NO_VAULT_KEY` — the request was right and this box cannot honour it).

**The fields read.** `entry[].changes[].value` gives `metadata.display_phone_number` (the door,
`+`-prefixed into E.164), `metadata.phone_number_id` (what a reply is sent FROM), `contacts[]`
(the profile name), and `messages[]` (`from`, `id`, `type`, `text.body`). Every model in
`whatsapp/inbound.py` carries `extra="ignore"` and requires nothing: Meta adds fields without
notice, and a strict model would turn each addition into a 422.

## One thread is one text call

Keyed by **(number, wa_id)** — the org's number and the person on it — in
`whatsapp/threads.py:Threads`. A thread holds:

- the `TextSession`, which is the very same class the chat socket runs (`session/text/session.py`);
- the **`phone_number_id`**, learned from the inbound and remembered here — the sender of the
  thread is wired from it. The Graph API sends from the id, not from the number, so nothing about
  a WhatsApp Business Account is stored in any table of this runtime;
- a queue and one pump task: one contact, one conversation, in order. Meta may deliver two
  messages a second apart and a model takes seconds;
- an idle clock.

`received()` puts the text on the queue and returns at once. Meta re-delivers a body its receiver
was slow to answer, so answering only after the model has finished would double every message.

**Opening a thread is opening a text call**, in the chat door's own order, which now lives in one
place both doors call: `session/text/opening.py:a_text_call()` — config through the pipeline
overrides, the org's own provider keys, the model, the quota, then the session. The differences
are at the ends: the agent is found by the DOOR rather than named in a URL, and every refusal is
one warning line and no session rather than a close frame.

**Who answers the number**: the operator's row first (`Routes.at("whatsapp", number)`), the app's
own declaration second (`Registry.at`). The same rule `GET /v1/routes` reads the two tables under,
asked from the other side — `routes add` moving a number with no deploy is the whole point of the
verb, so a declaration can never take it back. See `docs/decisions/routes.md`.

**The answer leaves from the LOG**, not from the model: `whatsapp/sending.py` watches the call and
sends every `turn.agent` with text in it. That is what makes a supervisor's `say` reach the
contact by the same path the agent's own answer does, with nothing remembering to send it twice.
`agent.transcript` deltas are never sent — a person on WhatsApp must not watch a sentence being
typed four times over. A `GraphRefused` is a warning line plus an `error` entry
(`code: whatsapp_not_sent`) in the call's own log, and the thread stays open.

## The token

`whatsapp` is a vendor in `domain/provider_keys.py:VENDORS`, and `providers/registry.py:KEY_OF`
maps it to `WHATSAPP_ACCESS_TOKEN`. So the Meta token is read through the very same `a_key()` every
model vendor is read through: the org's own when it brought one
(`pinecall-runtime orgs provider-key set <org> whatsapp`, which needed no CLI change), the box's
otherwise, and `NoProvider` when neither exists. A box with no token anywhere opens **no session
at all** — a call whose answer could never leave is never paid for.

The token is asked ONCE, at the moment the thread opens, out of the same key set the model was
built from: the vault is read once per call and not once per thing that call needs. It is never
logged, never in a refusal sentence, and never stored outside the vault.

## Two hours, and twenty-four

- `IDLE_SECONDS = 2 * 60 * 60`. Two hours with no inbound closes the thread:
  `hangup("timeout", "platform")`, the log seals, the judges run, and the next message opens a NEW
  call.
- `WINDOW_SECONDS = 24 * 60 * 60`. Meta only lets a business send free-form text within 24 h of the
  customer's last message.

**The idle close is what honours the window.** A thread can only answer while it is open, and it
can never be open more than two hours past the last inbound — so it can never be more than two
hours into a twenty-four hour window. There is no second timer and no "was this within the window"
check anywhere in the code, because the first constant makes the second one unreachable. A test
asserts the relation, which is what keeps `WINDOW_SECONDS` a fact and not a comment; the idle
period is a constructor parameter, so a test waits out two hours in a twentieth of a second.

## A human on a thread

The six supervise verbs act on a WhatsApp call exactly as they act on a chat one, through
`gateway/supervise/aiming.py` and `session/text/supervising.py`. What is specific to this channel:
a supervisor who takes the thread has no microphone, so `say` IS how they write to the contact —
it lands `supervisor.said`, then a `turn.agent`, and the watcher above puts it on the wire from
the org's own number. The whole rule is in `docs/decisions/supervise.md` →
"The same six verbs on a text call".

## What is NOT built

- **Voice notes and media.** Only `type: "text"` is read; anything else is acknowledged, dropped
  with one line, and opens no session. Transcribing an audio note through the call's own STT is a
  later card.
- **Templates.** Sending outside the 24 h window needs an approved message template and a
  different endpoint. Nothing here dials out.
- **Mark-as-read and typing indicators.** Both are extra Graph calls per message and neither
  changes what the log says happened.
- **The wamid.** Meta answers an accepted message with its own id. It is dropped: the only thing
  it is good for is joining a delivery receipt to the message it belongs to, and this door reads
  no statuses. The card that reads them mints the join.
- **Dedupe by wamid.** Meta re-delivers a body its receiver was slow to answer, and this door
  keeps no record of the `wamid`s it has already read: a redelivery inside the same thread is
  another turn. `Inbound.message_id` carries the id so the card that fixes it has it.
- **A WhatsApp screen.** The desk reads `GET /v1/agents/{slug}/calls` like any other channel.
