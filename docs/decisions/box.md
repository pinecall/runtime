# The box

Why the machine that answers the telephone is shaped the way it is. The how — the files, the
path a call takes, the fences — is `infra/box/README.md`; this is the argument behind it.

## The box is declared, and on no cloud in particular

`infra/box/` held four shell scripts on 2026-09-08 — `setup.sh`, `first_run.sh`, `remote.sh`,
`firewall.sh`, 350 lines between them — and every one of them did something systemd already
knows how to do from a file. On 2026-09-09 they left, and what is there now is one file per thing
the machine reads:

| the script did | the file that says it |
|---|---|
| `useradd` the service user | `sysusers.d/pinecall.conf` |
| `install -d` eleven directories with owners and modes | `tmpfiles.d/pinecall.conf` |
| generate secrets once into an env file | `pinecall-secrets.service` → `pinecall-runtime box secrets`, into systemd's credstore |
| `gcloud compute firewall-rules` | `nftables.conf`, on the host |
| `docker compose up` | `containers/*.container`, Quadlet |
| install and enable the units | `Makefile` |
| `migrate up` after the first deploy | `ExecStartPre=` of the gateway's unit |
| issue the worker's key into the env file | `pinecall-worker-key.service` |
| apt, uv, the deploy account | `cloud-init.yaml` |

The rule under the table is the one this whole repository is built on: **a file says what is; a
script says what to do**, and a machine that is a set of files can be read, diffed against the
repository and rebuilt, while a machine that is the result of scripts can only be remembered.

And it is on no cloud. `firewall.sh` was `gcloud`; a customer on Hetzner, on a bare machine in a
cupboard, could not have used it. The fence is on the host now, in nftables, and the first boot
is cloud-init — which every provider takes and which a bare machine takes through a seed — so the
same directory stands a box up anywhere. A cloud's own firewall in front is welcome and is not
relied on.

## The secrets are credentials, and nothing on the disk is in the clear

`/etc/pinecall/pinecall.env` used to hold every secret the box had, `0600`, read by two units
and by compose. It was also the file a deploy could silently overwrite, the file a `pg_dump` and a
copied env made a stolen tenant of, and the reason `setup.sh` needed a paragraph on why `[ -f ]`
lies to the account that runs it.

Every secret is a **systemd credential** now: a file named exactly as the credential under
`/etc/credstore.encrypted/`, encrypted under the machine's own key and sealed to its TPM where
it has one (`systemd-creds encrypt --with-key=auto`), decrypted by systemd into a private
directory for the one unit that named it (`ImportCredential=`), readable by that process and by
nothing down the tree. `_settings.py` reads that directory as it reads the environment, under the
same names, because pydantic's `secrets_dir` matches files the way the environment source does —
so a unit's `ImportCredential=DATABASE_URL` reads exactly like its `EnvironmentFile=` line did.

Two facts were measured on the box on 2026-09-09 before this was written, because neither is in
the manual:

- **The filename is the credential's name, with no extension.** `PINECALL_OPS_KEY.cred` is refused
  — "embedded credential name does not match filename" — and a refused credential under
  `ImportCredential=` is silently absent. The verb writes `PINECALL_OPS_KEY`.
- **`ImportCredential=` takes a trailing glob**, so a unit says `PINECALL_*` once, and the vendors'
  keys are listed by name because their names share nothing.

The three LiveKit containers cannot read a credentials directory the way the runtime does, so they
are handed **one** credential, `media.env`, as their environment file — `EnvironmentFile=%d/media.env`,
where `%d` is systemd's own specifier for the credentials directory, and Quadlet passes it
through to `podman run --env-file` untouched. It is written by the same verb, from the same draw,
as the runtime's individual credentials: two spellings, one generation.

The two keys are the case that decides the shape. `keys issue` is the one place a key exists in
the clear, and it prints the key on **stdout alone** and its two lines of context on stderr — so
a unit with `StandardOutput=file:` captures the key and nothing else, and `ExecStartPost=+` (root)
encrypts that file into the credstore and shreds it. No shell, no pipe, no journal. `migrate up`
consequently mints nothing any more: it runs before every start of the gateway, and a verb that
runs there must never print a secret into a journal.

## The units run the virtualenv, never uv

Both units used to `uv run --frozen --extra runtime …`, and the comment beside that line — in
four places — warned that `uv run` makes the environment match its arguments EXACTLY, so a unit
that asked for less than the other would uninstall the other's half on its next restart and the
phone would go quiet with a clean log. Four copies of a warning is a design telling you it is
wrong.

The units run `/opt/pinecall/venv/bin/pinecall-runtime`. The environment is built in one place,
the deploy's post-sync, as the service user, with the extras written once. A unit cannot uninstall
anything, because a unit never calls uv.

## The media plane is Quadlet, and the gateway waits for Postgres

Compose was declarative already; what it could not do was tell systemd anything. The gateway's
unit said `After=docker.service` and nothing about Postgres, so the order was luck. Each
container is a systemd unit now (`containers/*.container`, generated by podman's Quadlet at
`daemon-reload`), the gateway `Requires=pinecall-postgres.service`, and — since Podman 4.9 cannot
yet gate a dependency on a container's health — its `ExecStartPre=+podman healthcheck run
pinecall-postgres` refuses to start the gateway until the database takes a connection, and
`Restart=always` asks again three seconds later. The Postgres image is ours (pgvector and
pg_textsearch) and is built on the box by a oneshot with a stamp per tag, because 4.9 has no
`.build` unit; when it does, that unit becomes one file.

A deploy restarts the runtime's two units and **not** the containers: the media plane stays up
through a deploy, and a changed `.container` takes effect on its next restart, which is a person's
to time between two calls.

## The box holds no credential for the repository

The code arrives by **rsync, from a checkout, pushed by a person** — `shipway.yml` — and the box
cannot clone, cannot fetch, and has no identity on GitHub at all. It was briefly built the other
way, with a read-only deploy key generated on the box, and that is the wrong shape for one reason:
this machine answers the telephone from the open internet, on a SIP port whose whole design is
four fences. A machine in that position should not be able to read our source, and a credential
that lives there is a credential that survives every rotation of ours.

Two accounts, two owners. `/opt/pinecall/app` belongs to the account rsync arrives as — made by
cloud-init, because that account is yours and `tmpfiles.d` cannot name it. The virtualenv, the
cache and the recordings belong to the service user, which `tmpfiles.d` does name. Ownership
follows who writes, not who reads.

## The firewall says DROP out loud

The host drops what no rule accepts, so the explicit drop on 5060 changes nothing about what gets
through. It is there to be *read*, and it carries a counter: `nft list table inet pinecall` shows
how many INVITEs the fence has turned away, which is the fence demonstrably working rather than a
rule somebody hopes is there.

The carrier's networks live **in the fence itself**, as the `carrier_signalling` set.
`infra/tools/carrier_cidrs.py` reads that set for the inbound trunk rather than a second list, so
the firewall and the trunk are physically incapable of disagreeing about who may ring this box —
and the table stands beside podman's own and never flushes them, because a published port is
DNAT'd to a container before it reaches `input`.

## The manifest is a Makefile

Everything under `infra/box/` has to be put where systemd, podman, Caddy and nftables read it,
and that is the one imperative act a deploy performs. It is written as a Makefile with one rule
per file, because that is what a Makefile is for and what every Unix daemon's install has looked
like for forty years: a table of files and destinations that `install` overwrites when they
changed and leaves alone when they did not. The deploy runs `make install` and `uv sync`, and
nothing else.

`systemd-confext` — an overlay of a whole `/etc` from a directory — would make even that line
disappear, and is the direction; on systemd 255 it makes `/etc` read-only while merged, which
breaks `apt`, so it waits for the `--mutable=` of 256 to reach an LTS.

## The trunk tool stops instead of being clever

A carrier's fraud detection reads patterns. Create-delete-recreate loops, nightly provisioning and
bursts of API writes are what an account takeover looks like from the outside, and a precautionary
suspension takes the phone line with it. So `infra/tools/twilio_trunk.py` creates a trunk
**once**: finding one with the same friendly name, it prints what it found and exits 0 without
writing anything. It contains no delete and no loop around a create. Nothing on the box provisions
anything on a timer.

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
turning them on is a decision, and a decision is a human typing the command the tool prints.

## The probe asks the SFU, not the socket

`infra/tools/sip_probe.py` sends one INVITE, and one only. A `200 OK` proves the box answered; it
does not prove the call became anything. So the probe asks LiveKit whether a room appeared after
the ACK, which is the first moment the whole chain — fence, `hide_inbound_port`, inbound trunk,
dispatch rule, fleet — has demonstrably run. It sends no RTP: the audio is the one thing that still
needs a telephone, and a carrier will not let a number on the same account call itself (`21216`),
so there is no self-test over the PSTN to have instead.

## The tools have their own two config files

`infra/tools/` is Python that is not the runtime, so it carries a `.ruff.toml` that *extends*
`pyproject.toml` and lifts exactly two rules — printing, which is what a terminal program does,
and the ban on reading the environment directly, which speaks about a runtime that has a
`Settings` class to read it through. A carrier's credentials are not in that class and should not
be. `pyrightconfig.json` exists because the tools import each other by module name, which needs
the directory on the search path; the mode is the same `strict`. `scripts/lint` and
`scripts/test` run them with everything else.

## What was deliberately not taken

- **No cloud provider's API anywhere.** Not for the firewall, not for the machine. A provider's
  console or its own tooling creates the VM and pastes `cloud-init.yaml`; from there the box does
  not know where it is.
- **No NixOS.** The purest spelling of "a machine is a file", and the right one for a fleet of
  identical boxes; for one box and the people who touch it, it changes the OS, the vocabulary and
  every tool at once.
- **No Kamal, no compose on the box.** Kamal needs a registry and puts a proxy where the media
  is; compose cannot tell systemd what depends on what.
- **No nightly timer.** Not for evals, not for provisioning: anything on this box that spends
  money does it because a person asked in that moment.
- **No template rendering.** The configs are shipped as they are; the two values that change per
  box arrive in `/etc/pinecall/box.env`, written by cloud-init from the three lines a person
  fills. A `sed` over a `.tpl` is a config file nobody can read on the box and diff against the
  repo.
- **No embedder.** Memory arrives with its own milestone; a box that retrieves nothing should not
  be holding 2.3 GB of weights.
