# WhatsApp — Meta's webhook, one conversation per contact

A WhatsApp number answers like a phone number: a route of channel `whatsapp` says which agent,
and a conversation is a written call on the app that holds that agent, in the route's environment.

## The platform's Meta app

One Meta app serves every org's numbers on a deployment. Its secret (every webhook body is signed with
it), the handshake's word and a token replies go out on when an org brought none are the sealed
row `credentials/whatsapp` of `box_settings`: `{app_secret, verify_token, access_token?}`. A deployment
without the row answers `503` at the webhook. An org that brings its own number at Meta adds it as
an account (`PUT /v1/carrier {"kind": "whatsapp", "phone_number_id", "access_token"}`,
[numbers.md](numbers.md)) and replies to its contacts go out on its own token.

```
console → Numbers → Add a number → WhatsApp
  {"number": "+59829000000", "agent": "recepcion", "channel": "whatsapp", "account": "1055…"}
```

The number a WhatsApp account answers at is asked of Meta: `GET /v1/numbers/available` lists it
beside the Twilio numbers, with the account it lives in, and `POST /v1/numbers {number, agent,
channel: "whatsapp", account}` routes it. A token Meta refuses (a temporary one expires in a day;
a system user's does not) leaves the account listed with no number, said in the gateway's log.

## The webhook — `GET` · `POST /v1/whatsapp/webhook`

`GET` is Meta subscribing: `hub.mode=subscribe` and `hub.verify_token` equal to the platform's word
(compared in constant time) echo `hub.challenge` back as plain text; any other word is `403`.

`POST` is a delivery. The signature (`X-Hub-Signature-256: sha256=<HMAC-SHA256 of the raw
body>`) is checked on the bytes as they came, before anything reads them: `403` when it does not
match. Past it the answer is `200 {received: n}`, because Meta disables a webhook that keeps
failing: a receipt, an image, a number nobody routed are a line in the log and nothing else.

Meta delivers a message again when it thinks it unanswered, for up to 7 days, and each message is
read once. Its id is claimed per org (`whatsapp_seen`) before it is read, and the claim is the
insert itself, so of two deliveries at once, to one gateway or two, one reads it; a later delivery
of a message read is `200` and dropped. A message is read once it is on its conversation's queue
or kept for nobody (below). A reading that fails gives the claim back and the delivery is `500`,
so Meta's next one reads it; a delivery that arrives while another is still reading it is `503`,
so Meta keeps it until that reading is done; a claim a process died holding is read again by the
first delivery ten minutes after it. The nightly retention run forgets the ids past 7 days.

## A conversation

Each message goes onto its contact's conversation, keyed by the org, the environment, the number and the
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

The console's Calls ▸ Threads (`/v1/agents/{slug}/threads`) shows every contact of an agent, their calls merged into
one thread. A person with `talk` writes on the open conversation with
`POST /v1/agents/{slug}/threads/{contact}/messages {text}`: `202 {contact, call}`, sent as the
agent. `409` when the contact's newest call is not WhatsApp, when the window closed, or when the
conversation went quiet and is sealed. A supervisor's `takeover` on the call holds it: the
contact's messages are kept on the log and the model is not asked until `release`; `end` ends the
conversation and the log says a supervisor did.
