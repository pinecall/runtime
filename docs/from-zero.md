# From zero: a runtime of your own, and an agent answering

Every command on this page was run, in this order, against a database made for the purpose — on a
laptop, which runs the same runtime a server does. **The outputs are what came back**, with one
edit: the run was on port 8099, beside a gateway already using 8080, and the port is written here
as the default 8080. Where something refused, the refusal is here too — that is the half you will
actually meet.

Four repositories, side by side. The names matter: `runtime` resolves the wire through
`../protocol/python` and `agents` through `../protocol/typescript`, so a directory renamed on the
way in is an install that cannot find it; and `scripts/console` copies the browser pages from
`../agents` and the widget from `../widget` (`PINECALL_AGENTS` and `PINECALL_WIDGET` point
elsewhere).

```console
$ mkdir pinecall-v2 && cd pinecall-v2
$ git clone https://github.com/pinecall/runtime.git
$ git clone https://github.com/pinecall/agents.git
$ git clone https://github.com/pinecall/protocol.git
$ git clone https://github.com/pinecall/widget.git
$ ls
agents  protocol  runtime  widget
```

```
~/pinecall-v2/
  runtime/     the gateway and the worker — this repo
  agents/      the framework you write an agent in, its CLI, and the console
  protocol/    the wire, generated into all three languages
  widget/      <pinecall-widget>, one script, which the gateway serves to any site
```

**Both halves are published, and this page still clones them.** That is deliberate, not an
oversight: the walkthrough runs the example agent and the dev stack's `docker-compose` file, and
neither of those travels in a package. What a package is for is the day you write your own agent
in an empty directory —

```bash
pip install pinecall     # pinecall-runtime: the gateway, the worker, migrate, doctor
npm i -g pinecall        # pinecall: the CLI, and the console it serves
```

— and for a server, where `pip install pinecall` is the whole install. Everything below runs out
of the three checkouts, which is also how you change one and see it immediately.

What the machine needs beforehand: **Docker**, **[uv](https://docs.astral.sh/uv/)**, **Node 24**
and **pnpm**, and one API key of each role (`llm`, `stt`, `tts`) — §1 says which.

[the-runtime-cli.md](the-runtime-cli.md) and the agents repo's `docs/the-cli.md` are the reference
pages for every verb and flag. This page is the order you meet them in.

A machine with a domain on it, that answers telephones, is
[a-box-in-production.md](a-box-in-production.md).

---

## 1. The services

```bash
cd runtime
docker compose -f infra/compose/dev.yml up -d     # livekit · sip · redis · postgres · tei
uv sync --extra runtime --group dev
cp .env.example .env                              # then fill in the provider keys
```

A spoken call needs one key of each role — `llm`, `stt`, `tts`. **Everything up to
[Spoken calls](#spoken-calls) needs only the first one:** the text session builds no ears and no
voice, so an `ANTHROPIC_API_KEY` alone carries you through most of this page.

| role | default | variable | where the key comes from | needed for |
|---|---|---|---|---|
| `llm` | Anthropic, `claude-haiku-4-5` | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/settings/keys) | everything — this one alone is enough to start |
| `stt` | Soniox | `SONIOX_API_KEY` | [console.soniox.com](https://console.soniox.com) | a spoken call |
| `tts` | ElevenLabs, `eleven_flash_v2_5` | `ELEVEN_API_KEY` | [elevenlabs.io](https://elevenlabs.io/app/settings/api-keys) | a spoken call |
| embeddings | the `tei` container | — | nothing to buy — it runs locally | `knowledge push` and lookups; see the note below |

**They are yours, and each one is an account of your own.** Nobody hands you a key for this: a
walkthrough that ran on somebody else's key bills them for your reading and leaves their secret in
your shell history. The first row is the only one you need to get started, and the walkthrough
below ran on nothing else.

Those are the defaults, not the only choice: `.env.example` lists every vendor the runtime knows
and each keeps the vendor's own variable name, and `pinecall pipeline set --llm …` points a role
at another one. Two that cost an afternoon: your shell may already export `ELEVENLABS_API_KEY`, and
this runtime reads `ELEVEN_API_KEY`; and `pinecall-runtime doctor` knocks at every key you put in
`.env`, so it — not a failed call — is where you find out one of them is dead.

> **On an M-series Mac the `tei` container cannot start** — its CPU image has no arm64 build. Set
> `EMBED_PROVIDER=perplexity` with a `PERPLEXITY_API_KEY` and lookups embed over HTTP with no
> container. That is what this walkthrough ran on. With neither, `knowledge push` answers
> `503 TEI at http://127.0.0.1:8081 did not answer` and a lookup is skipped and said in the call's
> log — no call fails for it.

## 2. The schema

> **`role "pinecall" does not exist` is not your database talking.** A Postgres installed natively
> on the machine answers `127.0.0.1:5432` before the container does, and it has no such role. Point
> the URL at the container by name: `DATABASE_URL=postgresql://pinecall:pinecall@[::1]:5432/pinecall`.

```console
$ pinecall-runtime migrate up
applied 0001_call_log.sql
…
applied 0024_a_calls_corner.sql
org default has no key yet — `pinecall-runtime keys issue --org default` mints one
```

The migration seeds one org, **`default`**, and that is the org this walkthrough uses. A second
tenant is further down the page.

## 3. The gateway

The two browser pages — the console and the operator admin — are built from the agents checkout
next door and copied in as package data, and the widget is copied beside them from the widget
checkout. A fresh clone has never run that, so run it once:

```console
$ scripts/console
console → src/pinecall/gateway/console (4 files)
admin → src/pinecall/gateway/admin (3 files)
widget → src/pinecall/gateway/widget/pinecall-widget.js
```

Skip it and the gateway still comes up, and answers every page with a sentence telling you to run
it. That is the right refusal, but it is a step, not a surprise. The widget is then served at
`/widget/pinecall-widget.js` with `Access-Control-Allow-Origin: *`: a site anywhere loads
`<pinecall-widget>` from this gateway, as from a CDN. With no widget checkout the script stops at
`no widget checkout at ../widget: set PINECALL_WIDGET`, after the two pages are already copied.

```bash
pinecall-runtime gateway
```

It needs `DATABASE_URL` with the schema applied. **A gateway with no database verifies nothing**,
says so at startup and answers every keyed door `503` — it does not come up looking healthy. There
is no second mode: a laptop runs the same Postgres, the same migrations and the same issued keys a
server does.

## 4. The first person

```console
$ export PINECALL_OPS_KEY=$(openssl rand -hex 32)
$ pinecall-runtime init --email berna@clinica.test --person "Berna"
org default is already there
m_b3796f3579fc  berna@clinica.test  admin  runs this box
  http://127.0.0.1:8080/invitations/inv_…

  Open the link above to set a password. Then, in the directory of an agent:

    pinecall login http://127.0.0.1:8080
    pinecall run
```

`init` is the whole bootstrap: the org, its first **admin**, and that person made an **operator**
of this box — somebody has to be able to make the second org, and on a fresh runtime there is
nobody else. Run it twice and it carries on to the person rather than stopping at the org.

`PINECALL_OPS_KEY` opens `/v1/ops/*` and nothing else. It belongs to no org and is not a login.

**`init` is an HTTP call, which is why the gateway comes first.** It knocks at
`PINECALL_GATEWAY_URL` — `http://127.0.0.1:8080` unless `.env` says otherwise — so with nothing
listening there it cannot reach anything, and with *somebody else's* gateway listening there it
answers `401: this door is the box's`. That 401 is worth reading twice: it means the key was
refused, not that the command is wrong.

Open the invitation link and set a password. That browser now holds a key of its own, in
**production**, which is the world a console is for.

## 5. Sign a terminal in

```console
$ cd ../agents/examples/clinica-norte && pnpm install
$ pinecall login http://127.0.0.1:8080

open this to sign in:
http://127.0.0.1:8080/cli?c=cli_…

waiting…
▸ local · http://127.0.0.1:8080 · org default · sandbox
```

The terminal prints a word, the browser approves it, and the terminal collects a key **of its
own** — minted for the same person, labelled as this machine, revoked on its own from the Keys
screen. No password is ever typed into a shell.

**The key it keeps opens the sandbox.** A laptop is where things are written.

It lives in `~/.pinecall/config.json` (0600) and nowhere else:

```console
$ pinecall config
▸ local  http://127.0.0.1:8080  default · sandbox
$ pinecall whoami
gateway http://127.0.0.1:8080 · key from profile
org default · key k_29c915320fcf · sandbox · berna-air
```

`pinecall use <name>` switches; `--profile <name>` goes to one for a single command. No
environment variable is read: exporting `PINECALL_API_KEY` changes nothing.

For a machine with no browser — CI, a container — `pinecall login --key-stdin <url> < key` writes
the same profile from a key `pinecall keys issue` minted.

## 6. The agent

```console
$ pinecall run
clinica-norte · default · sandbox · connected to http://127.0.0.1:8080 · key from profile · tools 5 · doors phone +34910000000, whatsapp +34910000000, web
console  `pinecall serve` opens it on this machine (or `pinecall run --serve`)
line     rings in this terminal
```

One line, and it says the four things that decide where you are: the agent, **whose org**, **which
world**, and where the key came from. `pinecall run` binds no port. What it holds is in the
sandbox, and the sandbox is watched on your own machine: `pinecall serve` — or `pinecall run
--serve`, both in one terminal — puts the console on `http://localhost:4100`, forwarding every
request to this gateway with the terminal's key, so there is nothing to sign in to. The gateway's
own console, the one you sign in to with the password from §4, shows **production** and only
production: there the page says `no agent called clinica-norte is held here`, which is true. A
production `pinecall run`, on a machine key, prints that console's URL with a one-use code.

## 7. Talk to it

```console
$ pinecall chat               # another terminal, same directory
‹ hola, quería una cita con el fisio
› Clínica Norte, buenos días. ¿En qué puedo ayudarle?
› Buenos días. Para buscar su cita, necesito su nombre completo y un teléfono de contacto.
```

With nothing after it, `chat` mounts the agent of this directory in **this** process — the tools
run here, so a breakpoint in a `@tool` is reachable. Named an agent, it mounts nothing and is only
the caller's side, from any directory:

```console
$ cd /tmp && pinecall chat clinica-norte
‹ hola
› Clínica Norte, buenos días. ¿En qué puedo ayudarle?
› Buenos días. ¿Cuál es su nombre completo?
```

That is the first call. Everything below is a feature, in the order you meet it.

---

## The two worlds

A key opens **one** world — `production` or `sandbox` — and every agent it holds, every call it
takes and every fact it writes is that world's.

```console
$ pinecall run --env production
--env production asks for production, and this key opens sandbox: a key opens one world and no flag changes that.
  `pinecall use <profile>` for a key that opens production — `pinecall config` lists them.
```

`--env` **asserts**; it never selects. Nothing said means the sandbox, so a deployment types
`--env production` out loud — the deliberate act it should be. An agent that lands in production
because of whichever key happened to be active is the accident this exists to prevent. A console
has no such choice either: the gateway's shows production, and a machine's own the sandbox.

## Whose corner is whose

The sandbox holds **one agent per person**: two developers each run `clinica-norte` and neither
takes the other's. What each reaches — the console, `chat`, the config door, a suite — is their own
socket. A key that opens `team` (an admin's, the box operator's) is answered every member's corner,
each row saying whose:

```console
$ curl -H "Authorization: Bearer $ADMINS_KEY" localhost:8080/v1/agents
{"agents": [{"slug": "clinica-norte", "channels": ["phone", "web", "whatsapp"],
             "holder": {"holder": "m_6bb3ec66bf2b", "name": "carla@clinica.test"}}]}
```

That admin holds no agent of their own and can see what the team is running. The console's front
page draws the same thing with a **whose** column and a filter — *everything · mine · the team's*.
A developer sees none of it: there is one corner and nothing to filter.

And the admin can open one. In the sandbox, the header `pinecall-corner: <member id>` answers any
HTTP door in that member's corner — the console sends it when an admin opens a developer's copy —
so the agent, its line and its calls are Carla's:

```bash
curl -H "Authorization: Bearer $ADMINS_SANDBOX_KEY" -H "pinecall-corner: m_6bb3ec66bf2b" \
     localhost:8080/v1/agents/clinica-norte/sessions
```

A key without `team`, a production key, or an id that is no active member of the org is refused
`403`.

## Reading calls

```console
$ pinecall sessions --limit 2
clinica-norte · 2 calls

  call_55b9bcf1065d…  web inbound   2s  caller_hung_up  €0.0021  Perfecto. Le llegará un SMS con la cita…
  call_4b9cfa90e232…  web inbound   5s  caller_hung_up  €0.0064  Perfecto. Voy a reservarle esa cita…

$ pinecall sessions call_55b9bcf1065d4576a151fa97ac08fc57
  outcome   Perfecto. Le llegará un SMS con la cita del martes a las cuatro de la tarde…
  ended     caller_hung_up · 2s · 2 turns
  cost      €0.0021
  score     not judged: no judge was given to this session
```

The list is **your corner's** calls: the world your key opens, and whose. On a sandbox key that is
the calls you made; a colleague's test calls are theirs, and the telephone's are production's.

## The pipeline, and its knobs

```console
$ pinecall pipeline
  hears     soniox · es
  decides   anthropic · claude-haiku-4-5-20251001
  speaks    elevenlabs · EXAVITQu4vr4xnSDxMaL · es
  greeting  "Clínica Norte, buenos días. ¿En qué puedo ayudarle?"
  llm_node_ttft 0.64s · e2e_latency 0.00s

$ pinecall pipeline set --llm openai
  decides   openai   ← turned: llm

$ pinecall pipeline clear llm
  decides   anthropic · claude-haiku-4-5-20251001
```

A knob turned here takes effect on the next call, with no deploy. `pinecall providers` lists every
vendor this build can reach and which of them has a key — `livekit`, `anthropic`, `openai`,
`soniox`, `deepgram`, `elevenlabs` and thirty-nine more, each `ready` · `no key` · `no plugin`.

## What the agent knows

```console
$ pinecall knowledge push
clinica-norte · 2 files · 9 chunks · 1221 ms
$ pinecall knowledge list
clinica-norte · 9 chunks · pushed 2026-09-14 14:17
```

```console
$ pinecall chat --as +34600000001
‹ hola, soy Ana García, teléfono 600000001. Soy alérgica a la penicilina.
› Perfecto, Ana. Voy a buscar su ficha.
  La tengo localizada. Veo que tiene cita el jueves a las diez con la doctora Vidal…

$ pinecall memory +34600000001
- Es alérgica a la penicilina. (alergias)
```

A web caller is nobody until somebody says who they are — `--as` is this terminal saying it. Both
the base and the facts are **your key's world's**: a push on a sandbox key replaces the base your
own `run` answers from, never the one the telephone answers from. `pinecall memory forget` erases
a contact.

## Holding it to a golden

```console
$ pinecall test
clinica-norte · 11 goldens · anthropic/claude-haiku-4-5-20251001
  ✓ consulta-el-dia-que-nombra-aunque-ya-tenga-cita-ese-dia  e2e_latency 3415ms · llm_node_ttft 830ms
  ✗ en-el-chat-ofrece-mas-de-dos-horas
        says  broken   the agent never said 'nueve', 'once y media', 'cinco de la tarde'
        log            call_fc5e4e7029a44123a0f7858b360326fb
  …
  8/11 · 0 judge calls · 0.0378 EUR · 31s
  3 reproductions written to .pinecall/evals/run_9c9fa5c00b67/
```

It exits non-zero on a miss and writes a reproduction per failure. Three more rings:

```console
$ pinecall personas list
alta-y-reserva  educado y tranquilo, contesta lo que le preguntan sin adelantarse…
apurado         frases cortas, interrumpe, da el dato justo y pide la hora ya…
desconfiado     educado y receloso, responde con otra pregunta, no da datos hasta entender…
spanglish       empieza en inglés y termina en español…

$ pinecall simulate --persona apurado
‹ Perfecto, a las cinco me va bien. Mi nombre es Ana García y mi teléfono es 600 000 001.
→ propose({"slot":"s-20260915-1700-ev"})
› Perfecto, Ana. Le propongo el martes a las cinco de la tarde con la doctora Elena Vidal. ¿Se la confirmo?
‹ Sí, confírmala. Muchas gracias.
→ book({"slot":"s-20260915-1700-ev"})
› Listo. Recibirá un SMS con la confirmación…
  call_a27ee2e0bf4e… · 4 agent turn(s)

$ pinecall eval call_55b9bcf1065d4576a151fa97ac08fc57
  consent   passed    no irreversible tool ran in this call; 0 tool call(s) did
  register  skipped   no words were declared for this call
  errors    passed    the call logged no error
  latency   passed    llm_node_ttft 0.572s <= 1.000s; e2e_latency 1.295s <= 2.000s

$ pinecall remember
clinica-norte · anthropic/claude-haiku-4-5-20251001 · 3 cases · 3 held · 2844 ms
  ✓ anota la alergia y nunca la tarjeta
  ✓ la mañana sustituye a la tarde, no convive con ella
  ✓ ni guarda un permiso ni borra lo que nadie desmintió

$ pinecall runs list
run_9c9fa5c00b67  2026-09-14 14:17:48  clinica-norte  done  8/11
```

`runs show`, `runs diff` and `runs drift` read those back; `runs promote` turns one real call into
a golden.

## Where a ring lands

```console
$ pinecall line
rings in this terminal
$ pinecall line from +34600123456
calls from +34600123456 reach this terminal
```

A number exists once in a world, so a call at it rings in one place and which one is said out
loud. With nobody running it: `nobody is answering clinica-norte: start \`pinecall run\``.
`line claim` takes it, `line release` hands it on.

`line from` is your own phone, and it reaches your terminal at **both** numbers. At a sandbox
number, every call it makes lands in your corner whoever holds the line, as long as you are
holding that agent. At the **production**
number — the one the customers dial — the worker asks the gateway before it builds the call, and
while you hold the agent in the sandbox your phone rings in your copy: your declaration, your
tools, a sandbox log that says `diverted_from: production`. Every other caller reaches production.
Stop `pinecall run` and your phone reaches production too; a gateway that cannot be asked leaves the
call there as well.

## Numbers, and staging for nothing

```console
$ pinecall numbers list
no number answers in this world: `pinecall numbers import <+34…> --agent <slug>`

$ pinecall numbers import +34910000000 --agent clinica-norte --dry-run
the gateway answered 404: this org has no carrier yet: PUT /v1/carrier with a Twilio account or a SIP peer first
```

Bring the carrier (`PUT /v1/carrier`, or the console's Numbers screen) and `import` wires it: the
carrier's trunk pointed here, the SFU's trunk admitting the number, the route — `--dry-run` prints
those steps and writes nothing.

Then the verb that is the whole reason there is no third world:

```bash
pinecall numbers move +34910000000 --env sandbox      # → the sandbox, for an afternoon
pinecall numbers move +34910000000 --env production   # → back
```

An org buys **one** number. Pointing it at the sandbox is how a team tries a new agent on the real
line: one row, in effect on the next call, with the carrier account and both trunks untouched. A
move to where it already is writes nothing and says so.

## The team, and the keys

```console
$ pinecall keys issue --label "the prod server" --scope app
pk_…
  production · the prod server · app
  copy it now: the gateway keeps the fingerprint, and the key is never shown again

$ pinecall keys list
c2e5f051e67f  production  the browser          Carla          live
28ae52d33e03  sandbox     berna-air            Carla          live
7e4887c70518  sandbox     anas browser         Ana            live
ea98de57a943  production  the prod server      a machine      live

$ pinecall keys revoke ea98de57a943
revoked ea98de57a943
```

A key issued here is a **machine's** — a server, a CI job. People get keys by logging in, and a
person's key does not open `app` in production at all: what holds a deployed slug is a key issued
for a server. Revoking keeps the row, so the calls that key wrote stay readable.

The second person is the console's Team screen, or:

```console
$ pinecall-runtime orgs invite default carla@clinica.test --name "Carla" --role developer
m_8d5b70019dd4  carla@clinica.test  developer  invited
  http://127.0.0.1:8080/invitations/inv_…
  send them this; it opens the console's password screen once, within a week
```

A person is their email on the box — trimmed and lower-cased — with one password across every org.
Invite somebody who already has one, from another org on this box, and there is no link to send:
the row prints `active`, and under it `already a person on this box: seated, they sign in with the
password they have`. A login that names no org lands in the oldest of theirs; the console's org
switch moves between them.

**A role is a preset of scopes and nothing more.** A developer opens `app · calls · evals ·
knowledge · memory · pipeline · supervise · talk`; `numbers`, `keys`, `providers`, `team` and
`usage` are a manager's and an admin's. So a developer typing an admin's verb reads:

```console
$ pinecall numbers list
the gateway answered 403: this key does not open numbers: it opens app · calls · evals · knowledge · memory · pipeline · supervise · talk
```

## Spoken calls

```console
$ pinecall-runtime worker dev             # another terminal, in runtime/
registered worker {"agent_name": "pinecall", "url": "ws://127.0.0.1:7880", …}
```

The worker is the process that answers a call with audio. It knocks at the gateway with
`PINECALL_WORKER_KEY` — one key, issued by a person, wherever it runs.

## A second tenant

```console
$ pinecall-runtime orgs add clinica --name "Clínica Norte"
org_fb00ba7794ac  clinica  Clínica Norte

$ pinecall-runtime orgs list
default           default  default
org_fb00ba7794ac  clinica  Clínica Norte

$ pinecall-runtime orgs quota clinica --agents 5 --seats 10
  minutes           —
  agents            5
  seats             10
  …
```

`quota` replaces the whole set: a limit left out is no limit. A slug belongs to the first org that
registered it — which is how an agent lands in `default` by accident, since a box's own keys are
issued there. `orgs move` undoes it, and refuses while somebody is holding the slug:

```console
$ pinecall-runtime orgs move clinica-norte clinica
409: agent clinica-norte is held right now: stop it, move it, and start it again

$ pinecall-runtime orgs move clinica-norte clinica      # with the `run` stopped
clinica-norte → org clinica · 14 logs
```

The agent's own log, one head row per call it has taken, and its numbers all go with it.

## On a server

Nothing above changes. `runtime/infra/box/` is the declared box — cloud-init, the systemd units,
the Quadlets — and `make deploy` from a checkout is rsync + install + restart + `doctor`. Three
differences:

- the gateway and the worker are systemd units reading systemd credentials, not a shell;
- `PINECALL_ROLE` says what the machine runs: `all` · `hub` · `worker`;
- the agent's process runs on a key issued **for that machine** (`keys issue --scope app`), never
  on anybody's login.

`infra/box/README.md` is the box itself, credential by credential.

---

## When something refuses you

Every one of these was met while writing this page, and each names the next move.

| you see | it means |
|---|---|
| `no gateway: pinecall login …` | this machine knows no gateway. `pinecall config` lists the ones it does |
| `this key opens production, and \`pinecall run\` answers in the sandbox unless you say so` | the profile in hand is the wrong world. `--env production`, or `pinecall use` |
| `403 this key does not open numbers: it opens …` | your role's preset. An admin or a manager holds that scope |
| `503 TEI at … did not answer` | no embedder. `EMBED_PROVIDER=perplexity`, or start the container |
| `no database: a key is verified against the api_keys table` | no `DATABASE_URL`, or the schema was never migrated |
| `404 this org has no carrier yet` | bring one with `PUT /v1/carrier` before importing a number |
| `409 agent <slug> is held right now` | stop the `pinecall run` holding it, then move it |
| `404 no key of this org begins with <word>` | `pinecall keys list` prints the fingerprints it takes |

`pinecall whoami` answers the question under most of them: which gateway, which org, which world,
and where the key came from.
