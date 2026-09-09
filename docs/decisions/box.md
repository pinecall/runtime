# The box

Why the machine that answers the telephone is shaped the way it is. The how — the commands, the
path a call takes, the fences — is `infra/README.md`; this is the argument behind it.

## One machine, two processes, four containers, and the ring

A box carries the **evaluation ring**, and that is not a development convenience: ring 4 is the
product. The gateway scores every finished call at hang-up — `call.score`, `evals/score.py` — so
a gateway without the judges installed writes `not_judged` on every call the box ever answers, and
`/v1/evals/*` answers 503 to anyone who asks. It is also nearly free: `pinecall-evals` depends on
`pinecall[runtime]` and nothing else, and the judging itself is `livekit.agents.evals`, which
livekit-agents already brought. So `evals/` is one of the two directories a deploy rsyncs, beside
`runtime/` — and since ms-14 there is no third: the gateway is an API and serves no page.

## One machine, two processes, four containers

The worker and the SFU sit on the same box because they exchange the caller's media for the whole
call: a hop across a datacentre is not a number in a dashboard, it is heard in every turn.

The media plane (livekit, sip, redis, postgres) is compose, because those four are somebody else's
software pinned to a version. The two processes we write are systemd units, because they are the
things a person restarts, reads a journal of, and deploys twenty times a day. Putting our own code
in a container on the same box would buy nothing and cost a build on every deploy.

## The box holds no credential for the repository

The code arrives by **rsync, from a checkout, pushed by a person** — `shipway.yml` at the root of
this tree — and the box cannot clone, cannot fetch, and has no identity on GitHub at all. It was
briefly built the other way, with a read-only deploy key generated on the box, and that is the
wrong shape for one reason: this machine answers the telephone from the open internet, on a SIP
port whose whole design is four fences. A machine in that position should not be able to read our
source, and a credential that lives there is a credential that survives every rotation of ours.

So the deploy is `shipway deploy`, and the only account it involves is the one that already has
ssh to the box. It carries no build step: the gateway is an API, it serves no page, and the box
runs Python and nothing else. The console is a laptop's — `console/`, built where pnpm is and
served on 127.0.0.1 by `pinecall ui` for the life of that command — so the box never runs a
bundler and never holds a bundle.

Two accounts, two directories, drawn along that line. `/opt/pinecall/app` belongs to the LOGIN
account, because rsync arrives as that account. `/opt/pinecall/venv` belongs to the SERVICE user,
because `uv sync` writes it as that user — which is why `UV_PROJECT_ENVIRONMENT` points out of the
tree rather than leaving a `.venv` inside it. Ownership follows who writes, not who reads.

## The environment file belongs to the box

`/etc/pinecall/pinecall.env` is created once by `setup.sh` — the LiveKit keypair, the database
password and the ops key generated *there*, with `openssl`, and never printed — and no deploy
script ever writes it. The provider keys are added on the box by a human.

This is a lesson with a shape: a deploy that ships the laptop's `.env` over the box's silently
deletes every line the laptop does not have. It is not the copying that hurts, it is that the
missing line is only noticed by the next caller. So the rule is absolute rather than careful.

Two parsers read that file — systemd's `EnvironmentFile` and docker compose's `.env`, which
`setup.sh` symlinks to it — so it holds bare `KEY=value` lines: no quotes, no `export`, and no `$`
in a value, which compose would expand.

And the test that decides whether it is already there is `sudo test -f`, never `[ -f ]`. The
directory is `0750` and owned by the service user, so the login account running the script is
*refused* the file rather than told it is there — and a plain test then reports "no such file" on
a box that has one. The first real box found this the expensive way: every re-run of `setup.sh`
rewrote the environment from scratch, rotating the LiveKit keypair, the database password and the
ops key, and dropping every provider line a human had added. Postgres remembers the password its
volume was initialised with, so what the operator saw was `InvalidPasswordError: password
authentication failed for user "pinecall"` — a sentence about a password that names nothing that
rotated it. Every read of that file from a script now goes through `sudo`, for the same reason:
being refused a file and finding no line in it are the same exit code and very different facts.

## The units are furniture; the code is the deploy

`setup.sh` installs and **enables** both units and starts neither: on a fresh box there is nothing
under them yet, and a unit enabled over an empty `/opt/pinecall/app` restart-loops and reads as a
broken machine. The first `shipway deploy` is what starts them, and every deploy after that is
what restarts them.

What a box needs exactly once — the schema, the default org's key, and the fleet key the worker
knocks with — is `infra/box/first_run.sh`, run after that first deploy. It replaced two deploy
scripts that also carried the code, which is now shipway's job and only shipway's.

## Both units ask for the same environment

One virtualenv serves the gateway and the worker, and `uv sync` makes an environment match its
arguments *exactly*. A unit or a deploy that asks for less than the other uninstalls the other's
half — the vendors a call needs — and the phone goes quiet with nothing in either log to say why.
So `--extra runtime` appears in both units and in the shared deploy step, and the comment saying
why is in all three places, because that is where somebody will be when they are tempted to drop
it.

`--group evals` travels with it, in the same four places and for the same reason, and `--frozen`
with both: a box installs what `runtime/uv.lock` says and re-resolves nothing. Every path source in
that lock has to exist for a resolution to finish, which is what `Distribution not found at:
/opt/pinecall/app/evals` was saying on the first box before `evals/` was shipped there.

## The firewall says DENY out loud

GCP denies ingress by default, so the explicit deny rule on 5060 changes nothing about what gets
through. It is there to be *read*: the allow rule alone looks like a preference, and the pair
looks like a fence. It also means an edit to the allow rule cannot leave the port quietly open —
the deny is still standing behind it.

The carrier's networks live in one file with one reader. `infra/box/firewall.sh` calls
`infra/scripts/carrier_cidrs.py` rather than parsing the list itself, so there is no second parser
to drift: the firewall and the inbound trunk are physically incapable of disagreeing about who may
ring this box.

## The trunk script stops instead of being clever

A carrier's fraud detection reads patterns. Create-delete-recreate loops, nightly provisioning and
bursts of API writes are what an account takeover looks like from the outside, and a precautionary
suspension takes the phone line with it. So `twilio_trunk.py` creates a trunk **once**: finding one
with the same friendly name, it prints what it found and exits 0 without writing anything. It
contains no delete and no loop around a create. Nothing on the box provisions anything on a timer.

Adopting one is the same rule from the other side. A trunk somebody else built can already own the
number and be wrong in one field — the address it sends the INVITE to — and standing a second trunk
beside it is the very thing the paragraph above refuses. So `--adopt` moves that field and nothing
else: no trunk created, no number attached or detached, no delete, exactly one write, and a refusal
that changes nothing when the trunk carries more than one origination URI. `docs/decisions/sip.md`
tells the story of the number that forced it.

`--dry-run` goes further than usual: it opens no socket at all. It needs no credential, which means
it can be read and run before the account has ever heard from us — and a plan that cannot make a
request cannot make the wrong one.

The two transfer toggles are read, reported and never written. They cost money on every transfer —
the transferred leg is billed as Origination *and* Termination for as long as it lasts — so
turning them on is a decision, and a decision is a human typing the command the script prints.

## The probe asks the SFU, not the socket

`sip_probe.py` sends one INVITE, and one only. A `200 OK` proves the box answered; it does not
prove the call became anything. So the probe asks LiveKit whether a room appeared after the ACK,
which is the first moment the whole chain — firewall, `hide_inbound_port`, inbound trunk, dispatch
rule, fleet — has demonstrably run. It sends no RTP: the audio is the one thing that still needs a
telephone, and a carrier will not let a number on the same account call itself (`21216`), so there
is no self-test over the PSTN to have instead.

## Caddy carries signalling and not media

TLS terminates in one place for two upstreams: LiveKit's signalling paths and the gateway. Media
never passes through it — a proxy in front of WebRTC is a proxy inside every call — so 7881/tcp,
7882/udp and the RTP range go straight at the host. Postgres is published on `127.0.0.1` only: the
log holds what a stranger said on the telephone, and nothing outside the machine has any business
reading it. The recordings directory is `0750` and owned by the service account for the same
reason.

## The scripts have their own two config files

`infra/scripts/` is Python that is not the runtime, so it carries a `.ruff.toml` that *extends*
`runtime/pyproject.toml` and lifts exactly two rules — printing, which is what a terminal program
does, and the ban on reading the environment directly, which speaks about a runtime that has a
`Settings` class to read it through. A carrier's credentials are not in that class and should not
be. `pyrightconfig.json` exists because the scripts import each other by module name, which needs
the directory on the search path; the mode is the same `strict`. Both are three lines and both are
run in the card's gates:

```
cd runtime && uv run ruff check ../infra/scripts && uv run ruff format --check ../infra/scripts
cd runtime && uv run pyright --project ../infra/scripts
cd runtime && uv run pytest ../infra/scripts/tests -q
```

## What was deliberately not taken

- **No nightly timer.** Not for evals, not for provisioning: anything on this box that spends
  money does it because a person asked in that moment.
- **No template rendering.** The configs are shipped as they are; the two values that change per
  box arrive as environment variables (`LIVEKIT_KEYS` for compose, `PINECALL_DOMAIN` for Caddy).
  A `sed` over a `.tpl` is a config file nobody can read on the box and diff against the repo.
- **No embedder.** Memory arrives with its own milestone; a box that retrieves nothing should not
  be holding 2.3 GB of weights.
