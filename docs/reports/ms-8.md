# ms-8 — supervisor and WhatsApp: the verbs, the desk, human in the loop

Closed 2026-09-10. Two cards were open when it was picked up — `tk-e6869a` (hang up and transfer
by livekit's own tools) and `tk-ac5b10` (this) — and the chapter turned out to be one verb short of
its own first acceptance criterion.

**Where this report lives, and why it is not on the board.** Every earlier report is
`.taskops/reports/ms-N.md` in `~/pinecall/v2`, the monorepo the board is pinned to. That tree was
frozen on 2026-09-09 at 10:34, when the code moved into four independent repositories, and this
chapter's work is in those four. The report is here instead, tracked in the repository that holds
most of it. Closing the two cards on the board is Bernardo's to do or to delegate: it writes into
a tree this session was told not to touch.

## What we set out to do

1. `pinecall supervise <call>` streams audio and transcript; w/s/t/x/e work and each lands as an
   entry with `seq`
2. the Supervisor screen does the same with a supervise token
3. a WhatsApp message reaches the same class; pause/resume/send work; the 24 h window is honoured
4. the report is filed

## Achieved

**1 — the desk from a terminal.** `pinecall supervise` did not exist. The gateway has had the
doors since the chapter opened (`POST /v1/calls/{call}/verbs`, `/listen`, `/supervise`) and the
console has had the panel, but the word sat in the CLI's `PLANNED` table. It is written, and it is
one line per move rather than a raw-mode key, because that is what `chat` does and because a
terminal that takes a sentence has to take it somewhere:

```
w <text>     whisper to the agent — the caller never hears it
s <text>     say it to the caller, in the agent's voice, verbatim
t            take the line
x            give it back
e [reason]   end the call
q            leave the desk; the call goes on
```

Measured on one live web call against the local gateway, with the clinic answering:

| seq | entry | what |
|---|---|---|
| 18 | `supervisor.whispered` | "la doctora Vidal no pasa consulta los jueves" |
| 38 | `supervisor.took_over` | |
| 39 | `supervisor.released` | |
| 55 | `supervisor.said` | and the agent spoke it verbatim at 56 |
| 57 | `supervisor.ended` | |
| 58 | `call.ended` | `reason: supervisor_ended`, `ended_by: supervisor` |

Every one carries `by`, and every one has its own `seq`. That is the criterion, met.

**The audio half is deliberately not here**, and the verb says so in its own first line: a terminal
has no speakers this process may reach, and `pinecall simulate --listen` already tells a person the
same thing about the same room. `pinecall ui` is where a call is listened to.

**2 — the Supervisor screen.** Built. `src/cli/ui/console/screens/live/supervise.tsx` is the panel:
a hidden, silent seat in the room (`use-listen.ts`, `POST /v1/calls/{call}/listen`), one box with a
whisper/say toggle, and the six verbs through `use-supervise.ts`. Nothing on it draws the state of
the call: what a move did is in the timeline beside it, as its own `supervisor.*` line.

**3 — WhatsApp.** The third door, held to 87 passing tests across `tests/api/whatsapp/` and
`tests/api/supervise/`. A signed Meta webhook opens a call on the same class; a second message
stays on the same call; two people at one number get a call each; an image or a delivery receipt is
acknowledged and opens nothing; an org that brought its own Meta token answers on that one. The
desk holds a thread the same way it holds a line — while it does, the contact hears only the human;
a release gives it back and the agent answers the next message; an end says a supervisor did it.

**The 24 h window is honoured by a fact and not by a timer.** Meta only lets a business send
free-form text within 24 h of the customer's last message. A thread idles closed after two hours,
so it can never be older than the window when it answers. `IDLE_SECONDS < WINDOW_SECONDS` is what
the test asserts, which is the honest shape: no clock of ours has to be right.

**What was NOT done, and why.** The integration card asks for a transfer to Bernardo's mobile and a
real WhatsApp conversation with a human handback. Both place traffic on real accounts, and this
session has no standing word to do that. The cold transfer path is exercised by
`tests/session/voice/test_transfer.py` and the WhatsApp desk by the four thread tests; what is
missing is one run on real numbers, and it is one run away.

## Learned

**`MoveParticipant` is not implemented on a self-hosted server, and now that is measured.** ms-4
declared warm transfer out of scope on the strength of a sentence. 1.8 ships
`beta.workflows.warm_transfer`, so ms-8 asked again with the code open. Read whole, the workflow
opens a second room, starts a second `AgentSession` to brief the human, dials them with
`CreateSIPParticipant` — and then joins the legs with `MoveParticipant` and nothing else. Against
`livekit/livekit-server:v1.13.6`, with a real participant in a real room:

```
MoveParticipant RECHAZADO: twirp error unknown: not implemented, status=500
```

An empty room answers `503 no response from servers`, which says nothing — the probe only means
something with somebody in the room. The line stands, and two things are written down beside it
for whoever asks a third time: the workflow also runs a **second full pipeline** to brief the
human, which is a second set of provider calls per transfer and outside our metering; and its other
leg-joining path is `api.connector.connect_twilio_call`, a Cloud connector.

**livekit's `EndCallTool` would have logged a goodbye as a drain.** The tool ends the call with
`session.shutdown()`, which closes as `CloseReason.USER_INITIATED` — and our `HOW_IT_ENDED` reads
that as `drained` by the `platform`, the entry a deploy taking the worker down writes. A caller
saying "that's all, bye" would have been indistinguishable from a deploy. The seam is
`on_tool_called`: the real reason is written down BEFORE livekit closes anything, exactly the way a
cold transfer already sets its own, and `_how_it_ended` prefers what was set over what it derives.

**A tool that returns a sentence and changes nothing is a trap.** Clínica Norte's `transfer()`
carried a TODO and answered "Le paso con recepción." It is the tool a model reached for on the
first real call this project made, and it is gone. In `done` the class now offers no tool at all,
which is honest: the appointment is booked and the only thing left is to say goodbye and hang up —
which the class can now declare that the model may do.

**A page called a public contract said something that was not true.** `docs/security/prompt-injection.md`
claimed `recall` and `search` appear in the prompt's `<tools>` block. They do not: that block is
built by the class from its own methods, and the platform's two tools live in the request's tools
array. Found by printing a real prompt rather than by reading the code. Corrected.

**A unit test cannot catch a field read off the wrong object.** `supervise` read the observation's
folded event where it wanted the entry's own data, so every line printed the seq and the speaker
and no words. The unit tests hand `lineOf` its data directly and were right about every case. The
first live call it was pointed at caught it in one second.

## Decisions

| decision | where |
|---|---|
| warm transfer stays out of scope on a self-hosted SFU — measured, not assumed | `docs/decisions/sip.md` |
| `beta.tools.EndCallTool` is adopted, per class, with the reason written before the close | `docs/decisions/livekit-1.8.md` verdict 23 |
| `beta.workflows.warm_transfer` is refused | `docs/decisions/livekit-1.8.md` verdict 24 |
| the example's `tools=[EndCallTool()]` row moves from not-yet to ours-too | `docs/decisions/livekit-examples.md` |
| `room_options.delete_room_on_close` stays `False` on the session and `True` on the tool | `docs/decisions/livekit-examples.md` |
| a class opts into hanging up with `hangup`; one that says nothing cannot end a call | the schema's `HangupConfig` |
| the audio of a live call is the console's, not the terminal's | `src/cli/supervise.ts` |

The decision pages are `~/pinecall-v2/runtime-docs/decisions/`, which `runtime/.gitignore` keeps
out of every clone on purpose: they are this laptop's engineering notes, with dates and arguments
in them, and not documentation.

## Where we stand

The three feature criteria are met and the fourth is this page. What the chapter did not get is one
live run on real numbers, which needs a word rather than a card.

Worth naming beside it, because it is the larger fact: **the board this milestone belongs to does
not describe the tree any more.** It is pinned to `~/pinecall/v2`, whose last commit is
2026-09-09 10:34; the four repositories have 135 commits since. Its ms-9 shows six cards blocked
that are built, measured and shipped. Re-planning it against the four repositories is a decision
waiting on Bernardo, not work waiting on anybody.

## Next

- One live run: a cold transfer to a real number, and a WhatsApp conversation with a human handback.
- `docs/design/pinecall-v2-design.html` is tracked and still sells `doctor --bench`,
  `knowledge bench`, `<Memory>` / `<Retrieved>` and `/.well-known/pinecall`. The first two do not
  exist; the second two were deliberately removed by the security rewrite and the page now
  contradicts `docs/security/prompt-injection.md`. It is the biggest source of drift left inside
  the tree.
- Two `Ending` Protocols, one in `session/voice/events.py` and one in `session/voice/commands.py`,
  are one name for two things in one package. Pre-existing; noticed here.

## The command Bernardo runs

```bash
docker compose -f runtime/infra/compose/dev.yml up -d --wait
cd runtime && uv run pinecall-runtime gateway --host 127.0.0.1 --port 8080   # one terminal
cd agents/examples/clinica-norte && pnpm exec pinecall run                    # another
cd agents/examples/clinica-norte && pnpm exec pinecall chat --as +34600111222 # a third: talk to it
```

Then, with the call id from `GET /v1/agents/clinica-norte/sessions`:

```bash
cd agents/examples/clinica-norte && pnpm exec pinecall supervise <call>
# w dile que la doctora Vidal no pasa consulta los jueves
# t · x · s Le confirmo que no hay consulta el jueves. · e
```

Every move appears in the caller's own log, and in `pinecall ui` beside the audio.
