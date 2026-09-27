# WhatsApp — Meta's webhook, one conversation per contact

A WhatsApp number answers like a phone number: a route of channel `whatsapp` says which agent,
and a conversation is a written call on the app that holds that agent, in the route's world.

## The box's Meta app

One Meta app serves every org's numbers on a box. Its secret (every webhook body is signed with
it), the handshake's word and a token replies go out on when an org brought none are the sealed
row `credentials/whatsapp` of `box_settings`: `{app_secret, verify_token, access_token?}`. A box
without the row answers `503` at the webhook. An org that brings its own number at Meta adds it as
an account (`PUT /v1/carrier {"kind": "whatsapp", "phone_number_id", "access_token"}`,
[numbers.md](numbers.md)) and replies to its contacts go out on its own token.

```
console → Numbers → Import
  {"number": "+59829000000", "agent": "recepcion", "channel": "whatsapp", "hooked": true}
```

## The webhook — `GET` · `POST /v1/whatsapp/webhook`

`GET` is Meta subscribing: `hub.mode=subscribe` and `hub.verify_token` equal to the box's word
(compared in constant time) echo `hub.challenge` back as plain text; any other word is `403`.

`POST` is a delivery. The signature (`X-Hub-Signature-256: sha256=<HMAC-SHA256 of the raw
body>`) is checked on the bytes as they came, before anything reads them: `403` when it does not
match. Past it every answer is `200 {received: n}`, because Meta disables a webhook that keeps
failing: a receipt, an image, a number nobody routed are a line in the log and nothing else.

## A conversation

Each message goes onto its contact's conversation, keyed by the org, the world, the number and the
contact, and messages are answered one at a time, in order. The first opens a written call on the
socket holding the agent; the next ones stay on it. Every durable `turn.agent` of that call — the
model's, the agent's `say`, a supervisor's — goes to the contact through Meta's Graph API, and a
send Meta refuses is an `error` entry `whatsapp_not_sent` on the call's own log.

A conversation quiet for two hours is over: its call ends `timeout` and the next message opens a
new one. Two hours is under Meta's 24-hour customer-service window, which only that close keeps. A
turn past the org's quota ends the conversation unanswered. A gateway that restarts takes up a
conversation that still had idle time left, from its log, with its history.

## Nobody holding the agent

A message whose agent no app holds waits on the agent's own log (`message.waiting`). The moment an
app declares the agent, what waited is answered, oldest first, and taken off the queue
(`message.taken` naming the call). A message older than the 24-hour window is taken off with no
call. A gateway that starts reads the queue back off the logs.

## The desk

The inbox (`/v1/agents/{slug}/threads`) shows every contact of an agent, their calls merged into
one thread. A person with `talk` writes on the open conversation with
`POST /v1/agents/{slug}/threads/{contact}/messages {text}`: `202 {contact, call}`, sent as the
agent. `409` when the contact's newest call is not WhatsApp, when the window closed, or when the
conversation went quiet and is sealed. A supervisor's `takeover` on the call holds it: the
contact's messages are kept on the log and the model is not asked until `release`; `end` ends the
conversation and the log says a supervisor did.
