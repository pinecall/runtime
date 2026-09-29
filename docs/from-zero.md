# From zero: a box, an org, an agent answering

The order you meet everything in, from nothing to a caller heard. Each step names the page that
is its reference. The runtime runs on a box, not on a laptop: a laptop writes agents and knocks at
a box.

## 1. A box

A VM, `make box`, `make deploy`: [a-box-in-production.md](a-box-in-production.md). The doctor is
green, the console answers at the box's name, and the box's providers row names a vendor for each
stage with a key the box holds (`/v1/ops/providers`, `/v1/ops/provider-keys/{vendor}`).

## 2. The first org and the first person

```bash
PINECALL_GATEWAY_URL=https://voice.example.com PINECALL_OPS_KEY=… \
  pinecall-runtime init --org clinica --email you@example.com --person "You"
```

The org, its first admin made an operator of the box, and the invitation link printed once. Open
it, set a password: that person holds the console now. Who a key is and how the two worlds are
told apart: [multi-tenancy.md](multi-tenancy.md).

## 3. A terminal, and a project

In the directory of an agent, with the tenant's CLI (the agents repo's `docs/the-cli.md`):

```bash
pinecall login https://voice.example.com     # a key for this device, minted when you sign in
pinecall link                                # which org this folder is; PINECALL_KEY into .env
pinecall start                               # this process holds the agent, in the sandbox
```

`agent.registered` lands on the agent's log and the console lists the agent. Your key acts in the
sandbox unless a request says production, and in production only when your row opens it.

## 4. Talk to it

`pinecall chat` is a written call to the agent your terminal holds; the console's chat is the same
door. Every entry of the call is its log: `pinecall sessions show <call>`, or the console.

## 5. What it runs on

The vendors, the models, the voice, the greeting, the cut of a turn, what is remembered: the
agent's settings, per world and per scope, versioned ([protocol/settings-api.md](protocol/settings-api.md)).
`pinecall agent set --llm anthropic/claude-haiku-4-5` in the sandbox is yours until you set it with
`--team`. What the next call would run, and how fast the last ones were: `pinecall pipeline`.

## 6. What it knows, and what it remembers

`pinecall docs push` a folder of Markdown as a knowledge base, `pinecall docs attach <base>` to the
agent, and a turn searches it; a memory policy on the agent keeps what each contact said for their
next call ([retrieval/spec.md](retrieval/spec.md)).

## 7. Holding it to a golden

`pinecall test` runs the agent's goldens through the process that holds it and judges each into a
matrix; `pinecall simulate --persona <name>` puts a simulated caller on the line
([protocol/evals.md](protocol/evals.md)).

## 8. A number

The org's carrier account, a number imported and routed to the agent, and a phone call rings it:
[protocol/numbers.md](protocol/numbers.md). A web page talks to it through the widget or a stock
LiveKit client and a token its backend mints: [protocol/tokens.md](protocol/tokens.md).

## 9. Production

A person with production access, or a server's key made for production in the console, runs the
same process against production: `pinecall start --prod` on a laptop, or the process you deploy
with `PINECALL_KEY` set. Production's settings are set there directly; the goldens run in CI before
a deploy, not at a door.

## When something refuses you

Every refusal is one sentence naming the fix, `{"detail": "…"}` under its status, and a quota that
refuses writes `credits.exhausted` on the agent's log. The ones you meet first: `403` a key that
does not open the door or the world; `404` an agent nobody holds; `429` a quota; `503` a box
missing something, the doctor says what.
