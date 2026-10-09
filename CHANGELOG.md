# Changelog

## Unreleased

## 0.1.29 — A call's trace says its world, its session and its contact as any tool reads them (2026-10-09)

- Every span of a call also carries the OpenTelemetry conventions `deployment.environment.name`
  (the world), `session.id` (the call) and `user.id` (the contact's id, never the number), so
  Langfuse, Datadog, Honeycomb and Grafana sort a trace into its environment, session and user
  without knowing what `pinecall.*` means (`worker/_traces.py attributes_of`).
- Console a6263fc: Settings ▸ Telemetry offers Langfuse by its region and two keys, beside any
  OpenTelemetry collector; the Erasures card names who asked.

## 0.1.28 — The charts in both themes, and the access log whole (2026-10-09)

- Console f4448e2: the charts' six tones are a palette the console has (four series drew in
  no colour, and on dark two were one violet); the tooltip, ticks and crosshair on real tokens.
- Data & privacy reads every kind of read the runtime records (listen, supervise, export, memory):
  one such row had refused the whole list.

## 0.1.27 — The webhook's secret is resealed with the rest (2026-10-09)

- `org_webhooks.ciphertext` joins the columns a vault key rotation walks; 0.1.25 and 0.1.26
  sealed it under the current key and would have left it behind on a rotation.
- Console 5dcd629: the Alerts tab's event labels get their width.

## 0.1.26 — The console's Alerts tab and the widget's kept conversations (2026-10-09)

- Console dc0403f: Settings ▸ **Alerts** sets the org's webhook, proves it with a test post
  and says what is posted; Notifications gains "a monitor fires"; Monitors says who set each
  one by name.
- Widget a5aacad: the visitor's last conversations kept in the browser and read again from the
  menu (`history="off"` keeps none); the panel redrawn.

## 0.1.25 — A webhook for the org's alerts (2026-10-09)

- **Webhook**: `PUT /v1/webhook` names a URL of the org's own; every alert — `monitor.fired`,
  `spend.unusual`, `credits.exhausted` — is posted there as it is written on the agent's log,
  `{type, org, env, agent, at, data}`, signed with `x-pinecall-signature: sha256=<hmac>` when the
  org set a secret, tried twice within five seconds. `POST /v1/webhook/test` proves the URL.
  `monitor.fired` says its `env`.

## 0.1.24 — The console's Quality is a harness: judges and monitors as its tabs, Test opens on an overview (2026-10-09)

- Console 63bf329: Quality's judges table is a tab of its own beside **Monitors** — the
  world's monitors, set and dropped there, each with the last day it fired; an agent's Test opens
  on an overview of the harness: personas, simulations, judges, goldens, cases and monitors, each
  with its number, and the latest runs.

## 0.1.23 — Monitors: a number of the series watched, fired once a day (2026-10-09)

- **Monitors**: `POST /v1/monitors` watches one number of the series — end-to-end or first-token
  latency, the judges' held rate, the share a person took over, the share of tools that failed,
  spend or calls — over 1, 7 or 30 days, above or below a line, for every agent or one. At each
  call's seal the world's monitors are read; one that crossed fires once a day as
  `monitor.fired` on the agent's log and keeps the day and the value. `pinecall monitors` and the
  console's Monitors screen set them.
- The console's charts bridge a day with nothing by a dotted stroke, and every day with data
  gets a dot (console d491bcb).

## 0.1.22 — A call's length counts only when it ended after it started (2026-10-09)

- **A mean length, and an agent's minutes, count only calls that ended after they started**: one
  production call sealed with an `ended_at` before its start threw a day's mean to minus fifty
  million seconds on the Observability screen and the Overview.

## 0.1.21 — Observability's cards, one word each (2026-10-09)

- A card's seconds read as one word (`1.46s`), and its delta names the window short.

## 0.1.20 — org_telemetry under row-level security (2026-10-09)

- **`org_telemetry` gets the row-level security every org table has**: a tenant's connection
  sees its own telemetry row alone. CI had refused 0.1.14 to 0.1.19 for it, so none of them
  reached PyPI; production ran them from the image, where every door checks the org itself.

## 0.1.19 — Observability, the way a dashboard reads (2026-10-09)

- **The series say each day's mean call length** (`mean_length_s`), and the console's
  Observability screen opens on five cards — calls, success rate, time to first token, mean
  length, spend, each against the window before — then endings as stacked bars by day, the
  duration, the failure modes (the judges that said no, most often first), and a by-agent table.

## 0.1.18 — Observability, the axes right (2026-10-09)

- A judge's share tops out at 100%, and a day with calls shows its tool count, zero included.

## 0.1.17 — Observability, in the console (2026-10-09)

- **Observability, in the console**: `GET /v1/insights/series` answers the window day by day —
  calls, how they ended, cost, end-to-end and per-stage latency at the median and p95, each
  judge's held rate, tools run and failed — and the console's new Observability screen draws it,
  for every agent or the one in view, over 24 h, 7 d or 30 d. A call's tools are counted at its
  seal (`drift_calls.tools_ran`, `tools_failed`).

## 0.1.16 — The transcript says the word written, not how it is pronounced (2026-10-09)

- **The lexicon is read back in the transcript**: a word the voice was told to say another way
  (`Pinecall` → "pain-col") reached the log, the console and the judges as the spoken form.
  The transcript now carries the word written, with the spoken form's timing.

## 0.1.15 — Settings ▸ Telemetry, padded (2026-10-09)

- The console's Telemetry card reads like the form beside it.

## 0.1.14 — A call's trace id is the call's (2026-10-09)

- **A call's spans share a trace id a reader can compute**: the call id's 32 hex digits, or the
  first 32 of the SHA-256 of a carrier's call id — so a call found in the console is found in the
  org's own tracing tool by the same id. The console's call page names it, with a copy, when the
  org exports (console `screens/call/trace-id.tsx`).

## 0.1.13 — The console gets Settings ▸ Telemetry (2026-10-09)

- **Settings ▸ Telemetry** in the console: the org's collector set with its headers (sent once,
  read back by name alone) and whether a span may carry what was said, and stopped from the same
  card — the console's `screens/telemetry`, over `/v1/telemetry`.

## 0.1.12 — An org's traces reach its own collector (2026-10-09)

- **An org sends its calls' traces to its own collector**: `PUT /v1/telemetry` names an OTLP
  endpoint, the headers every export carries (sealed in the vault, never read back) and whether
  a span may carry what was said; the worker exports each call's spans there beside the box's
  collector, every span carrying `pinecall.org`, `pinecall.env`, `pinecall.agent`,
  `pinecall.call` and `pinecall.holder`. `pinecall telemetry` sets it from the CLI.

## 0.1.11 — A tool announces itself (2026-10-09)

- **A tool announces itself**: a tool declared with `announce` ("Let me check the agenda.") is
  said as it starts, while it runs, when the model's turn said nothing itself; a turn that spoke
  and then called the tool is not announced twice. `@pinecall/agents` 0.9.24, `pinecall` 0.1.8 on
  PyPI and the gem 0.0.4 declare it.
- **A golden's recall reaches the agent**: the facts a golden gives are written on the call as a
  real recall is (`memory.ops`), so the agent's `remembers(…)` reads them; before, only the model
  saw them and the view went on as if nothing were remembered.

## 0.1.10 — A golden's day reaches the agent (2026-10-09)

- **`call.started` says the day a golden pinned** (`today`, `YYYY-MM-DD`), so an agent resolves
  "on Monday" against the golden's day, as the model does, and not the clock's. Absent on every
  other call. An SDK before this field refuses the entry: `@pinecall/agents` 0.9.23, `pinecall`
  0.1.7 on PyPI and the gem 0.0.3 read it.
- **A golden that gives `memory` to an agent whose policy keeps nothing is refused** by name,
  before the run opens a call: recall runs only on an agent with a policy, so the facts would
  never have been recalled and the golden would have tested a memory the agent does not have.

## 0.1.9 — The runtime whole on one machine: `pinecall-runtime local up` (2026-10-09)

- **`pinecall-runtime local up`**: the runtime whole on one machine from the package alone — the
  compose files it ships written to `~/.pinecall-runtime/local`, Postgres, Redis and LiveKit up,
  the schema migrated, the secrets drawn once, then the gateway with the console and a sandbox
  worker in the foreground. `local down`, `local init`, `local env`. `make local` runs it.

## 0.1.8 — A hosted project starts on the newest CLI of its minor (2026-10-09)

- **A hosted project starts on the newest CLI of its minor**: the runner installs `pinecall@^0.9.40`
  when a pod starts, so a CLI release reaches hosted projects on their next start and no runtime
  release carries it. A CLI that breaks its contract bumps the minor, and the runner with it.

## 0.1.7 — A hosted project is started by the platform's own pinecall (2026-10-09)

- **A hosted project is started by the platform's own `pinecall`**, installed in the pod beside
  the project's dependencies at the version the runtime names (`CLI_VERSION`, 0.9.40), never from
  the project's `node_modules`: a project lists `@pinecall/agents` and nothing of the CLI.

## 0.1.6 — PyPI `pinecall-runtime`, and a call a judge broke on kept as a case (2026-10-09)

- **The runtime is PyPI `pinecall-runtime`**: `pip install "pinecall-runtime[voice]"`, `uvx
  pinecall-runtime …`. The import is still `pinecall`, and the command still `pinecall-runtime`.
  PyPI `pinecall` is now the Python SDK, from 0.1.6 on; 0.1.0–0.1.5 there are this runtime, so a
  `--from pinecall` without a version pin no longer finds the command.
- **A call a judge broke on waits as a case**: at hang-up, a call that did not pass is kept in the
  org's dataset as a `pending` case, its `expect` what the broken verdicts forbid (`consent` →
  `not_tools`, `grounded` → `grounded`, the model judges → `judges`), its golden carrying what
  `recall` gave the call. At most 50 wait per agent. `PATCH /v1/evals/cases/{id}` approves (the
  nightly's `dataset: true` plays only those), dismisses, holds out, or marks a case kept in the
  repository; `judge_was_wrong` writes the calibration label. `GET /v1/evals/cases` takes
  `status` and says how many wait. `GET /v1/calls/{call}/golden` answers the golden a call makes,
  kept nowhere. Migration `0097_case_status.sql`.
- **`expect.judges`**: a golden asks hang-up judges by name (`promises`, a compliance judge, the
  org's or the agent's own) of its call; a name the panel does not hold breaks the golden.
- **`GET /v1/callbacks` lists the key's world only**: an agent's log holds the callbacks of both
  worlds, and the list returned every one of the org's; each is now the world of the call it names.
- **A sealed call's usage entries carry their `type`**: the memory writer's and the simulated
  caller's were built without one and frames drop unset fields, so the SDKs rejected the seal of
  any call that wrote memory (`pinecall chat --as` ended in a ZodError).
- **A written caller can hang up**: `{"hangup": true}` on `WS /v1/chat` ends the call, then closes
  the socket with `the call ended: caller_hung_up`, so whatever served the call stops with nothing
  live. A caller that only closes its socket still ends the call, as before.
- **A drain counts only calls still live**: a call that ended and waits for its seal is neither
  handed on nor parked, so `pinecall chat` and `test` no longer say a live call was kept.
- **`pinecall remember`'s refusal names the memory policy**, which is the world's: `agent <slug>
  remembers nothing: its memory policy names nothing to keep`.
- **An agent's language is a setting of its world**, like its voice and its models: `language` in
  the settings body (`pinecall agent set --language en`, the console's Settings), a pipeline knob a
  `words` key cannot set, tried on the ears and the voice before it is kept. Unset, each vendor
  keeps its own default; an app on an SDK before 0.9.19 still declares one, read only when the world
  set none.
- **`call.started` says how the call is had**: `medium`, `voice` for a call in a room and `text` for
  a written one, so an app tells the widget's spoken call from its chat, which share the `web`
  channel. SDKs before 0.9.19 refuse a field they do not know: release them first.
- **A call opens in the state its opener asked for, on `call.started`.** `WS /v1/chat?state=<json>`
  (an object, 16 KB at most; anything else closes the socket with the sentence) and a golden's
  `state` ride the call's `call.started` as `state`, absent when nobody asked; the app applies it
  before the first render. The eval runner no longer sends a `session.configure` with the
  golden's state, so the log holds no `state.changed` the app did not write. A spoken call carries
  it too: a spoken golden's state and `POST /v1/evals/voice`'s new `state` ride the dispatch to the
  worker, which writes them on `call.started`.
- **Breaking: the console's directory verbs go to the socket that answers the console.**
  `agent.register` may say `answers_dev: true`, and `POST /v1/agents/{slug}/dev/{family}/{verb}`
  goes to the newest such socket that is not draining; `view.render` still goes to the socket
  serving the call. An agent held only by sockets that serve calls is `409`, naming
  `pinecall start`. Update the CLI with this runtime: an older `pinecall start` registers no
  companion, and the console's Chat, Tests, Simulations, Docs and Memory screens are refused.
- **The start-up rebuild of the SIP trunks tries again until whole**: a gateway started beside a
  LiveKit still starting (or down) left that world's numbers unadmitted until its own next start;
  it now retries, 2 s and doubling, a minute at most, while an org is refused or a LiveKit cannot
  be reached.
- **A developer's own phone reaches their sandbox copy across LiveKits.** Where each world has a
  LiveKit of its own, a ring from a developer's registered phone at a production number can no
  longer be handed to the sandbox's fleet in production's room, so production's room dials it to
  the sandbox's livekit-sip: `sip:<the production number>@<the sandbox's SIP name>`, the caller as
  its From, `X-Pinecall-Org`, `X-Pinecall-Agent` and `X-Pinecall-Holder` saying whose ring it is.
  The caller and that leg stay bridged in production's room, and the gateway deletes the room when
  either leaves, so hanging up either side hangs up the other. On the sandbox's LiveKit the start
  makes a `hand-over` trunk and rule: the trunk lists no number and admits production's media
  address alone (the one `PINECALL_SIP_DOMAIN` names) with a pair drawn from `LIVEKIT_API_SECRET`,
  turning the headers into the leg's attributes; the rule sends the leg to the sandbox's fleet, and
  the gateway offers it as the developer's call. `GET /v1/agents/{slug}/rings-for` answers the
  `trunk` to dial in that case (null where the worlds share a LiveKit, which keeps the hand-over in
  one room). A handed-over ring is now opened as a phone call at the production number, its
  caller read off its leg, in both cases; it was opened as a widget's. The firewall in front of the
  sandbox's SIP must admit production's media address on 5060.
- **A LiveKit per world.** A sandbox call never shares a machine with a production one: each
  world's rooms, trunks and rules live on a LiveKit of its own. The gateway reaches production's at
  `LIVEKIT_URL` and the sandbox's at `LIVEKIT_SANDBOX_URL` (unset, the sandbox shares production's,
  as before), on the one key pair, and asks each world's of its own: a room is offered on the
  LiveKit of its fleet's world, the reaper asks a quiet call's world's, a simulated caller joins
  its world's, and a number's trunk and rule are on its world's alone — moving it between worlds
  takes it off the old world's LiveKit and admits it on the new one's, and the start's rebuild takes
  a number off the other world's where it is still listed. LiveKit's webhook names the world that
  sent it, `POST /v1/livekit/webhook?world=sandbox` (none is production's; another word, `422`). A
  browser joining a sandbox call is told `wss://<the name>/sandbox` when the sandbox has its own
  LiveKit. `doctor` asks each world's LiveKit and names the one that does not answer.
- **One name, two worlds.** A box has one name (`PINECALL_DOMAIN`) and both worlds answer at it:
  the world of a request is its key's (a server's token opens the world it was made in) or, for a
  person, the `pinecall-env` header's, the sandbox when it names none — never the name the request
  came in by. `PINECALL_SANDBOX_DOMAIN` is gone, and with it the refusal of a header that disagreed
  with the name. The console is production's at `/` and the sandbox's at `/sandbox/…`, same
  origin, same sign-in; the gateway writes no `pinecall-world`/`pinecall-elsewhere` marks into the
  page, and `GET /.well-known/pinecall` no longer says `world` or `elsewhere`. A browser's
  `server_url` is `wss://<the name>` in both worlds. The SIP names stay one a world
  (`PINECALL_SIP_DOMAIN`, `PINECALL_SANDBOX_SIP_DOMAIN`): a carrier's, written into its trunk by the
  runtime, typed by nobody.
- **v1's machines left the repository**: the box, the cell, the fleet loop and its
  clouds, the Packer image, the AWS fleet and the lab's own Terraform, and with them `box up`,
  `box upgrade`, `box failover`, every `cell` verb, `fleet loop`, `fence apply` (`pinecall-fence`),
  the join door and its scope. The fence is the cloud's firewall: `fence export` prints the
  networks 5060 opens to as Terraform's `sip_sources`, and `GET /v1/ops/carriers` says them
  (`networks`, in place of `applied_at` and `applied`). A carrier sends a world's calls to its SIP
  name (`PINECALL_SIP_DOMAIN`, `PINECALL_SANDBOX_SIP_DOMAIN`; unset, the world's name), and
  `sip repoint` (`POST /v1/ops/sip/repoint`) sends every Twilio trunk at a world's name on to it,
  once. A worker registers with LiveKit under its name and its start, so a container started again
  under the same name is never taken for the dead one. A gateway keeps an idle connection 620 s
  (Google's load balancer keeps its own 600) and imports every plugin before it listens;
  `pinecall_vendor_failing` carries every installed vendor, 0 while it is sound. Uplift AI imports
  (`python-socketio`, which its plugin needs and does not declare).
- **The runtime runs in Kubernetes pods** (`infra/`), with the box's behaviour unchanged unless
  said: `PINECALL_GATEWAY_LISTEN` binds a pod's gateway on its network and
  `PINECALL_TRUSTED_PROXIES` names the load balancer it believes (a box keeps loopback and Caddy);
  `PINECALL_METRICS_FROM` lets a cluster's Prometheus scrape `/metrics`, never a forwarded
  request; `PINECALL_WORKER_HTTP_HOST` opens the worker's health port to a pod's probes; and
  `GET /v1/ops/fleet/{fleet}/wanted` tells KEDA how many scaled workers the fleet wants, by the
  loop's own line, counting the core's seats first. `PINECALL_WAL_SPOOL` names the box's WAL
  spool `doctor` reads (a cluster's Postgres archives through its operator, with none, and the
  application connects there as no superuser).
- **A chat in a room ends after ten minutes without a message.** It was the one call with no
  ceiling: a visitor who left a chat open held a worker's seat for as long as the page stayed
  open. It ends as `timeout`, the ten minutes the voice ceiling has.
- `gcp-mig.py delete` asks the group's list first and deletes only a machine still on it, instead
  of reading a refusal's text to tell one already gone.
- `gcp-mig.py list` leaves out a machine the group is deleting: it stays listed for about a minute
  after its delete returned, and the loop decided its delete again every tick (four times in
  production on 2026-10-03). The script's verbs have a test on a fake Compute API (`tests/infra/`).
- A worker's heartbeat names it to LiveKit (`agent_name`) only once LiveKit registered it: it beats
  from its start, and the gateway offered calls to a machine still loading its plugins, which waited
  12 s for the offer to go to another worker.
- **A machine the loop makes is named once**: `pinecall-worker-<yymmddhhmmss>-<n>`, the time it was
  asked for, instead of the lowest free `pinecall-worker-<n>`. A reused name carried the last
  machine's cordon and its silence in the roster onto the next one, which the loop deleted as
  "cordoned and gone" 13 s after asking for it (production drill, 2026-10-03).
- **A call whose caller dropped ends**, whatever the reason they left. livekit's session closes on
  the caller leaving only when they hung up, the room was deleted or they were rejected; a browser
  whose connection timed out was waited for, and with a supervisor watching the room never emptied:
  on 2026-10-03 a call held its worker's seat for over half an hour after its visitor was gone. A
  caller who does not come back within 20 s, LiveKit's own wait for a room's last person, now ends
  the call as `caller_hung_up`, the room deleted with it.
- **The fleet loop is the one thing that sizes a fleet.** Production's managed instance group had an
  autoscaler too, grown on the fleet's calls the loop wrote to Cloud Monitoring: it counted the
  calls the box's own workers held, asked for a machine at the first call, and the loop let it go
  as one too many, five machines in forty minutes on 2026-10-03. The autoscaler (and AWS's target
  tracking) is gone; `infra/fleet/gcp-mig.py create` makes a machine in the group by name and
  `aws-asg.py create` raises the group by one, as every other cloud script makes one. `measure`,
  `--grow-at-most 0` and the box's metric-writer role are gone with it.
- A release stops each world's fleet loop before it changes the package and starts it when it
  ends: it never restarted it, and the loop ran the code it was started with until it crashed.
- **The gateway chooses the worker a call goes to.** A worker registers with LiveKit under its own
  name, `<fleet>/<worker>`; the SIP rule and a visitor's token still dispatch to the fleet's plain
  name, which nobody holds, and the gateway, told by LiveKit's webhook that a person is alone in a
  room, offers its call to the worker heard in the last 12 s with the most seats free, again to
  another after 12 s, and to the fleet's overflow after three. Outbound calls, simulated callers
  and the sentence of a worker gone go the same way. The overflow is `<fleet>/overflow`, always
  open; its gate and `GET /v1/fleet/standing` are gone. `fleet list` and `/metrics` say the rooms
  waiting for a worker; each offer is a line in the gateway's journal with the worker and why, and
  `doctor`'s `offers` line names rooms no gateway swept. A room is let go when a worker opens its
  call, an agent joins it or it ends, so a caller who hung up first is never offered again; a
  fleet none of whose workers was heard yet (a gateway just started) has its room wait up to 12 s
  for the next sweep instead of the sentence. A box needs LiveKit's webhook to place calls.
- **The hosted apps' machine is Terraform's too** (`module "apps"`, imported with no change):
  nothing of the runtime's cloud is made by hand any more.
- **A worker that counts its calls takes every one of them.** It reported `calls ÷ slots` to
  LiveKit, whose server and framework both stop at 0.7: LiveKit refused it at 0.7 of its slots
  (6 of 8, 23 of 32) while the gateway counted the rest free, and the next call rang in silence.
  It now reports on LiveKit's scale, `0.7 × calls ÷ slots`, so LiveKit's full is its last slot,
  and `calls ÷ slots` to the gateway, full at 1.0; neither of LiveKit's lines is overridden, and a
  test reads the installed framework's to hold the worker to it.
- A machine of workers no longer takes the box's `PINECALL_IDLE_PROCESSES` with its fleet's
  settings: it keeps livekit's, one warm process per CPU.
- The lab stops a worker before it destroys its machine, keeps the machine's journal under
  `.lab/`, and places its calls at `--rate` a second. A machine destroyed with its worker up stayed
  registered in LiveKit for 15–20 minutes and took half the next run's calls into silence.
- Every machine of the cell is a `pinecall-runtime cell` verb, as the box is `box up`: on the box
  `cell allow-replica`, `allow-gateway`, `allow-worker` (an address or the fleet's range), their
  `forget-…`, `gateway-credentials`, `worker-settings` and `worker-credentials`; on the machine,
  `sudo uvx --from pinecall==<version> pinecall-runtime cell join-worker | image-worker |
  release-worker | join-gateway | release-gateway | join-replica`, which copies the package's
  `infra/` there first. Nobody copies `infra/` by hand or calls a script by its path any more; the
  scripts stay as what the verbs run. `pinecall-runtime --version` says the version to install.
- The curl examples are gone, from the repository and from the docs site.
- **The cloud is Terraform's** (`infra/terraform/`): the state in a versioned bucket, one root
  module per environment, `make tf-plan`/`tf-apply`/`tf-check` (CI runs the last). Production was
  imported whole — the box and its replica, the fleet's subnet, the firewall rules, the static
  address, the two buckets with their IAM users and policies, SES, the box's four names in Route 53
  — and a plan says `No changes.`.
- **A fleet machine on Google Cloud reads its credentials from Secret Manager** as its own
  identity: a worker service account per world, five secrets declared with no value
  (`modules/secrets`), written by the box with `pinecall-runtime cell publish-secrets`.
  `pinecall-runtime cell enroll` replaces the bash enroll: a join token, or Secret Manager, once.
- **Production's fleet is a managed instance group** (`modules/fleet-gcp`): the image built by
  Packer (`make image`, `infra/packer`), no public address (a NAT for the fleet's subnet), healed
  on the worker's health port, grown by its autoscaler on the fleet's calls; it never shrinks
  itself. `fleet loop --grow-at-most 0` is the loop of such a cloud: it tells the cloud the calls
  (`measure`) and lets go of the one too many once drained; `infra/fleet/gcp-mig.py` is its
  script, and `pinecall-fleet-loop@<world>` runs it on the box, configured from the box's
  metadata. The box's VM acts as `pinecall-fleet`, which may touch the fleet's machines alone.
- **The runtime whole on a laptop**: `make local` starts the box's Postgres, Redis and LiveKit in
  docker (`infra/local/`), migrates and writes `.local/env` once; `make local-gateway` and
  `make local-worker` run both from the checkout. No cloud account, no key of a box.
- **A fleet on AWS, the same shape as Google Cloud's**, written and validated, applied by no box:
  `modules/secrets-aws` and `modules/fleet-aws` (an Auto Scaling group grown by target tracking on
  `Pinecall/fleet_calls`, its scale-in off), `infra/fleet/aws-asg.py` for the loop, an
  `amazon-ebs` source in `infra/packer`; `cell enroll` and `cell publish-secrets` read and write
  Secrets Manager on AWS. `infra/fleet/aws`, the loop that made EC2 machines itself, is gone.
- **The voice lab is Terraform's** (`environments/lab`): `infra/lab/measure.py` makes the box, the
  generator and the worker machine with `terraform apply` at the sizes under test, resizes the box
  by the same apply, and destroys all of it with `terraform destroy`; the machines' configuration
  is `infra/lab/configure.sh`, one verb a step.

## 0.1.5 — The fleet's image carries no credential: each machine joins on a key of its own (2026-10-02)

- The fleet's image carries no credential. A machine the loop makes spends a join token at its first
  boot (`POST /v1/fleet/join`, `pinecall-join.service` → `worker.sh enroll`) for a fleet key of its
  own, the LiveKit pair and the store's secret, sealed to that machine; the loop mints the token
  (`POST /v1/ops/fleet/join-tokens`: a key of scope `join`, named for the machine, ten minutes, spent
  once) before each `create` and revokes the machine's keys after each `delete`
  (`DELETE /v1/ops/fleet/{worker}/keys`). `worker.sh image` prepares the machine the image is frozen
  from, from `primary.sh worker-settings` (no secret); `infra/fleet/first-boot` is the user-data the
  three cloud scripts hand over; `fleet loop` requires `--fleet` unless `--dry-run`.
  Measured on Google Cloud against the sandbox fleet: a machine made from such an image was
  `accepting` 99 s after the loop asked for it, and deleted with its key revoked 86 s after the
  cordon. The recipe is `infra/fleet/README.md`.
- The fence lets in a range (`primary.sh allow-worker 10.100.0.0/24`), the subnet the fleet loop
  makes its machines in; `infra/fleet/gcp` takes `PINECALL_FLEET_SUBNET`.

## 0.1.4 — Workers on machines of their own, the recording made by the call itself, a box measured with real audio (2026-10-02)

- Workers on other machines: `primary.sh allow-worker <address>` on the box (the fence, LiveKit's
  API and the gateways' balancer at the box's own address, `PINECALL_HERE`), then the fleet's
  credentials through a pipe into `worker.sh join <box> <wheel> <world> [calls]` on the other
  machine; `worker.sh release` for a new wheel. A worker machine reaches the box's LiveKit and
  gateways and nothing else of it. Measured on 8 vCPU: 24 calls at once at 3.5 cores (~0.15 vCPU a
  call, the recording included), first audio p95 1.3 s, every turn answered; the box spends ~0.1 a
  call on their media. `docs/scaling.md`, "A machine of workers alone".
- A call's recording is made by its own session, not by an egress process per call: livekit's
  recorder for the caller (left) and the agent (right) at 24 kHz, and every other voice of the room
  — a supervisor who took over, the far end of a warm transfer, the hold melody — laid in on the
  right where it sounded when the file closes, a stretch at a time. The worker seals it under the
  call's key and moves it to the bucket before the seal. Egress is no longer asked for anything
  (track egress was built and measured dearer: ~0.1 vCPU and ~170 MB a track). The lay-in costs
  ~1/40 of the call's length on a core; a call of half an hour or more with another voice may run
  past the worker's minute to seal, said on the box page.
- LiveKit's API is published on the box's own address by `install.sh`, behind the fence, so letting
  a worker or gateway machine in restarts nothing (a box from before gets one restart, said).
- The object store's file is `/etc/pinecall/store.env` (was `backup.env`): backups and recordings
  both read it. `box upgrade` (and `install.sh`) moves a box's `backup.env` there, once, and says so.
- The four alerts are evaluated on the box by Prometheus and mailed by Alertmanager
  (`infra/box/alerts.sh`, `alerts.sh test` proves the path).
- The box measured whole with real audio and no vendor (`infra/lab/`: the vendors faked on their own
  wire, SIP callers playing a recording): a 4-vCPU box with everything on it holds 6 calls at once.
  Three fixes it found: the plugins are preloaded in the worker's forkserver (ring to live 0.1 s, was
  3–5 s idle and up to 74 s loaded), SIP's RTP range matches the fence, and each world's seats are
  its own.
- Insights count a window of 1, 7 or 30 UTC days, of the scope or one agent.
- A chat's opening is kept, so the gateway holding the app's socket serves it.
- `replica.sh join` works on a machine made a minute ago.

- The box's workers count their calls (`PINECALL_MAX_JOBS`, one per vCPU each, written by `install.sh`)
  instead of reading the machine's CPU: on CPU, a burst of calls left rooms with no agent, LiveKit never
  offering them again (measured: 5 of 10 at 20 % CPU; counting, 14 of 15). A call costs ~0.24 vCPU.
- The disaster drills, run: a restore to a minute in 79 s with RPO under a minute, and a failover
  to the replica in 17 s with no write lost, a box again in under 3 minutes (`docs/a-box-in-production.md`).

## 0.1.3 — The server side, measured: gateways on many machines, a cluster of media, and the numbers (2026-10-02)

- A model that says nothing for 12 s (or the agent's `llm_timeout_s`) no longer leaves the caller in
  silence: the turn is cut and the caller hears a short sentence asking them to say it again, in the
  agent's language. Unset used to mean no deadline at all.
- LiveKit as a cluster, measured: two nodes on the box's Redis carried 400 voice calls with no packet
  lost, and a worker on one node took rooms placed on the other. How a node is added is on the box page.
- Gateways on machines of their own: `infra/cell/primary.sh allow-gateway` on the box (pg_hba, the
  fence, Postgres, Redis and LiveKit's API published on its address for those machines alone, Caddy
  sending them calls), `gateway.sh join` on the other machine (the credentials through a pipe). Redis
  asks a password of everyone (drawn by `install.sh`, `PINECALL_REDIS_URL` a credential). Measured: a
  gateway machine killed for two minutes under 1 200 calls lost none of them.
- What a container is published on beyond loopback is written into its installed file: podman 4.9
  reads no `.container.d` drop-ins, so the replica's Postgres (`primary.sh allow`) was never
  published on the box's address. Fixed for it and for gateway machines.
- A gateway that dies with a worker's request in flight loses no call: an open is asked again while
  the gateway is away and is the same call by its id (one claim, one opening, one ringing), and a seal
  asked again that finds the call sealed is done. The seal's lease is 15 s renewed while sealing (was 120 s):
  a gateway that dies sealing lets the call go in seconds, and a knock waiting on it takes the seal
  over, instead of the worker giving up and the reaper sealing the call with no usage. The lease
  names its holder (migration 0085): only it renews or gives it back, and a summary is written once
  per log, so two gateways sealing one call price it once. Measured: 1 200 calls held, a gateway killed every
  minute: 0 logs wrong, and the only refusals left were opens and seals in flight at the kill.
- `pinecall-runtime load` counts refusals by status and door (`502 POST /v1/calls`), not by status.
- A log read is built from its rows without validating them again (`log/store.py` `entry_of`): a seal
  reads its whole log three times. Measured from a generator machine at 300 calls held: append p50
  60 → 38 ms, p99 850 → 430 ms, seal p50 330 → 200 ms.
- The append path costs a gateway a third less of its core (100 calls held: 155 % → 100 % on two
  gateways; append p50 161 → 14 ms, p99 1.4 s → 94 ms): a group's rows go to Postgres as one JSON
  document instead of six arrays adapted a value at a time, the entries socket and the relay read and
  write JSON in pydantic's own parser, and only the types the facts fold lock a call's facts row.
- A worker's batches of a call go down one WebSocket for the call's life (`WS /v1/calls/{call}/entries`)
  instead of a request per batch: no routing, headers or key per batch on the gateway. A refusal is a
  frame and the socket stays; a lost socket is opened again and the batch asked again with the same
  `after`; a gateway of before (no such door) gets every batch by request, as before.
- A gateway remembers a key it verified for five seconds (`tenancy/remembered.py`): verifying
  was a round trip per request, 15 % of its core under load. A key revoked at a door is forgotten
  on every gateway at once, said on the signal, and so is a person whose role, agents, production
  access or operator standing changes; one revoked at a shell opens for those seconds.
- A summary or a score written before money in dollars (`eur`, `judge_cost_eur`, the euro's `rate`)
  is read again, its euros as the same number of dollars, as migration 0007 kept the facts: on a
  box that ran before dollars, most summaries had become unreadable. `facts rebuild` and doctor's sample
  leave a call whose log holds an entry the wire still refuses as it was folded, instead of
  refolding it to nothing.
- The providers row may name the language a vendor's ears are told (`tuning."stt/<vendor>".options.language_code`),
  and it wins over the call's base code: NVIDIA's streaming ASR takes `es-US`, not `es`.
- How a spoken turn ends is the ears' `tuning` too: `turn_model` picks the local model that reads
  the end of the caller's turn off the audio, `v1-mini` (livekit's, the default) or `smart-turn-v3`
  (Daily's Smart Turn v3, a new dependency, 8 MB of ONNX on the worker's CPU).
- The open stack: `infra/models/` holds three model servers for one NVIDIA GPU (`compose.yaml`:
  Whisper large-v3-turbo on Speaches, Gemma 4 12B and bge-m3 on Ollama, Kokoro-82M) and the
  providers row that points a box at them (`providers.json`); the wheel carries it as
  `pinecall/infra/models/`. `docs/the-open-stack.md` walks it from a bare GPU and has the numbers:
  about 1.6 s from the caller's last word to the agent's first on an RTX 3090, and why NVIDIA's
  streaming ASR is not the ears (its NIM drops sentences after the silences of a real call).

## 0.1.2 — A box from the package itself

- `pinecall-runtime box up --domains …` makes the machine it runs on a box, with no checkout: the
  wheel carries `infra/box` and `infra/postgres` as `pinecall/infra/`, and `box up` installs the
  system's packages, runs `install.sh` and releases this version from PyPI. `box upgrade` brings a
  box to the version it is run from, its names kept. `sudo uvx --from pinecall pinecall-runtime box up`.
- `release.sh` releases a PyPI version or a wheel's path (`PACKAGE=`) besides a built wheel (`WHEEL=`).
- `/usr/local/bin/pinecall-runtime` on a box runs any operator verb with the box's credentials.
- A box made from the package encrypts its backups to the key `--backup-key` gives, or makes none:
  the package carries no backup key, and `install.sh` enables the backup only with one.

## 0.1.1 — The runtime written again, in production

The runtime written again from a blank page, in production since 2026-09-29. `pip install pinecall`
installs the gateway, the worker and `pinecall-runtime`, the console and the widget inside;
`docs/from-zero.md` walks a box to its first call.

- The skeleton: the domain types, the error hierarchy, the settings, the wire, the database pool
  and the migration runner, the schema as one migration, and the rules `make check` enforces.
- The call's log: an append-only store whose seq is born in one upsert (a sealed log refuses an
  append with 409), the facts of each call folded in the same transaction, the reducer the
  TypeScript and Ruby SDKs share (held to the protocol's golden log at every cut), the two
  projections (the tenant's, with `pii` fields masked when read, and the public one), and one
  in-process fanout whose slow reader gets a `log.gap` with a snapshot.
- Fixed: the usage feed counted every call's tokens and characters as zero; it read rows named
  `llm`/`tts` while a summary carries `llm_usage`/`tts_usage`.
- Fixed: a reader of a log sealed without `call.score` waited for ever; the stream asks the store
  whether the log is sealed before it reads the backlog.
- The suites run on a local Postgres (`make test`: colima, the image of `infra/postgres/`, one
  schema per test); `make test-sandbox` runs them on the sandbox database.
- Every vendor livekit ships is a vendor here: each installed plugin that exports an LLM, an STT
  or a TTS, and LiveKit Inference, built through one path with its constructor's own argument
  names. The runtime keeps no list of vendors, models or voices; a model or a voice a vendor does
  not have is that vendor's own error, in the call's log. `pinecall[voice]` installs all but
  four; `pinecall[voice-big]` adds aws, azure, google and speechmatics.
- An org's own key runs any installed vendor and any model of it; the box's keys run what
  `quotas.lends` lends the org. What the box offers, each vendor's options, the defaults, the
  voices per language and the prices are one row of the database, edited from the console.
- Voice and written calls run on the same livekit session: the same turns, tools, prompt,
  supervisor verbs and entries. A written call's `agent.reply` is instructions for one turn, as a
  voice call's always was.
- One gateway and one database serve production and the sandbox. A person signs in once and
  has one key for both worlds (production only with production access); a server has a key per
  world, `pc_live_` or `pc_test_`. Test calls stay off production processes because each world
  has its own fleet of workers, and every call is dispatched to the fleet of its world. Quotas and
  an agent's hold melody are kept per world (migration `0002_worlds.sql`).
- What a new org is given is the box's `admission` setting, edited from the console; a box with
  none limits nothing. No extension package is loaded.
- `PINECALL_VAULT_KEY` is required: every secret is sealed under it, and the gateway does not
  start without one. `PINECALL_WORLD`, `PINECALL_ELSEWHERE_URL`, `PINECALL_IDENTITY_URL`,
  `PINECALL_SANDBOX_URL`, `PINECALL_SANDBOX_KEY`, `PINECALL_PEER_KEY` and `PINECALL_EXTENSIONS`
  are gone.
- Orgs, people, keys, sign-in (password, one-use codes, pairing, sign-up, an org's own OpenID
  provider), tuning and lexicon versions per corner, widgets, hold
  melodies, personas, caller codes and mail. Usage is counted from the calls' summaries when a
  call or a turn asks to start; nothing is held in memory.
- Fixed: the tuning and the lexicon a call is built on are read in one query, so a write between
  two reads no longer records a version the call did not run on.
- Fixed: a written call counted each turn twice (the caller's and the agent's); a tool's
  structured output reached the model as Python's repr instead of JSON.
- The gateway and the worker. One gateway serves both worlds: the app socket, the worker's doors,
  the log's readers (a page or a stream), the supervisor's seats and verbs, a visitor's room token
  with the dispatch to its world's fleet signed inside, caller codes, and text calls on
  `WS /v1/chat`. A worker is livekit's `AgentServer` registered under its fleet's name
  (`pinecall`, `pinecall-sandbox`); it resolves whose call a job is from its dispatch or the number
  dialled, opens the log, and runs the session. Workers report to the gateway every five seconds;
  a full production fleet is answered by the overflow, a full sandbox refuses at the token door.
  A call the worker lost is sealed by the gateway's reaper. The vendors a call ran on the box's
  key are kept with its facts (migration `0003_lent.sql`).
- The box: `infra/box/` makes a machine from nothing (cloud-init, `install.sh`, Quadlet containers
  for LiveKit, SIP, egress, Redis and Postgres, the units); `make deploy` builds the console into
  a wheel and releases it; `pinecall-runtime` has `gateway`, `worker start`, `worker overflow`,
  `migrate up`, `keys fleet` and `doctor`.
- Fixed: a tool of a call whose agent nobody holds waited until its deadline; it is refused at
  once.
- Numbers and WhatsApp. An org holds many carrier accounts (Twilio accounts, SIP peers, WhatsApp
  numbers at Meta), sealed under the vault key; a number is imported from one of them ("we hook
  it": the account's trunk pointed here, found by where it points) or hooked by the org itself
  ("you hook it": admitted from its networks, nothing outside touched). On the SFU an org has one
  trunk per fence and one dispatch rule per world, so a number moves between worlds by a door
  (`PUT /v1/numbers/{number}/env`) without touching its trunk. The box buys numbers into either
  world on its own Twilio account, capped by that world's `numbers` quota. The door paths and
  answers are v1's, so the console reads them unchanged. `0004_carriers.sql`.
- Dialling out needs no trunk on the SFU: every leg is dialled with its trunk inline, so an emptied
  SFU dials on. A Twilio account's termination is one per account and box, with a credential per
  org on it. The dial guards count and write the ledger row in one transaction under the org's
  lock, and a number is a stranger to a world it never called.
- WhatsApp conversations are keyed by the org, the world, the number and the contact; a message
  whose agent nobody holds waits on the agent's log and is answered when an app declares the
  agent, with no polling. The box's Meta app and Twilio account are sealed rows of
  `box_settings`, not variables: `PINECALL_WHATSAPP_APP_SECRET`, `PINECALL_WHATSAPP_VERIFY_TOKEN`
  and the three `TWILIO_*` are gone.
- Fixed: a number the box bought was never admitted again when LiveKit's Redis was emptied, and a
  purchase rewrote an org's SIP peer trunk with Twilio's fence; the reconcile at start rebuilds
  every routed number with its own fence.
- Fixed: a supervisor's `end` on a WhatsApp conversation left it open; the conversation closes
  with its call.
- A WhatsApp account's number is listed from Meta beside the Twilio numbers and imported on its
  account; an agent answers at as many numbers as the org routes to it, of any kind.
- The account doors, on the one gateway, at v1's paths and in v1's shapes: `/.well-known/pinecall`,
  `whoami`, sign-in with a password or a one-use code, the orgs a person opens and the switch
  between them, a terminal paired from a browser, a forgotten password, an invitation accepted,
  the org's members and keys, sign-up, the org's identity provider and its mailbox, and
  `/v1/ops/whoami`. The sandbox asking production who a person is (`POST /v1/login/redeem`) is
  gone with the second instance; box-wide Google sign-in answers `503`. A key minted for a device
  is labelled with it, and a login code gives a copy of the key that minted it, the person
  included, dying when it dies.
- Evals: a suite of goldens runs through the app that holds the agent, one written call per
  golden and model, judged into a matrix stored as it goes (`POST /v1/evals/run`); a finished call
  is checked by code (`/replay`) or judged again (`/judge`); a persona is played by a model, in
  writing (`/caller`) or on a spoken line (`/voice`). A persona names the agents it may call. Every
  call is judged at hang-up when its org judges, the box names a judge model and the ceiling is
  above zero; the judge's tokens are counted and priced. Eval runs belong to an org and a world.
- Money is in US dollars, the currency providers price in: a call's cost, each priced line, the
  judge's cost, a persona run's cost, the org's monthly budget and the judging ceiling
  (`PINECALL_JUDGE_CEILING_USD`). Nothing is converted at a rate; the providers row carries no
  exchange rate. A persona names the agents it may call on the wire too.
- Retrieval in the call: a turn's recall and search run on the gateway for the worker
  (`POST /v1/calls/{call}/lookup`) and for a written call alike, every attached base searched in
  one pass, the contact's facts recalled when the agent keeps memory, `docs.sources` and
  `memory.ops` written on the log where they ran; a lookup that cannot run is a recoverable
  `<tool>_skipped` and the turn goes on. The seal writes what the call taught into the contact's
  memory between `call.ended` and `call.summary`, within `PINECALL_REMEMBER_BUDGET_S`, and a
  hang-up that cannot remember says so and seals all the same (`POST /v1/calls/{call}/remember`
  does the same on request).
- The rest of the doors, so every door of v1 answers: the org's meters (`/v1/usage`,
  `/v1/insights`, `/v1/limits`), an agent's settings and the org's lexicon versioned per world and
  scope, the pipeline report and the hold melody (uploads converted once to Ogg Opus), the widget,
  the providers catalogue as each org may run it, the org's own vendor keys, a vendor's voices and a
  sample, and the operator's `/v1/ops/*`: orgs, their people, keys, quotas per world, dial guards,
  the SSO break-glass, routes, the fleet and its cordons, every org's floor and meter, the box's
  mail and brand. The fleet loop (`fleet/hub.py`) grows and shrinks a fleet through a cloud script.
  `pinecall-runtime` gained `init`, `orgs`, `keys`, `routes`, `fleet`, `sessions`, `providers`,
  `memory reembed`, `migrate status` and `migrate plan`. Box-wide Google sign-in answers 503.
- The box's own configuration has doors: the providers row (`/v1/ops/providers`), the box's vendor
  keys (`/v1/ops/provider-keys/{vendor}`), admission (`/v1/ops/admission`) and the fleet of each
  world (`/v1/ops/fleets`); `pinecall-runtime providers seed` writes a box's first providers row.
  `infra/fleet/` holds the clouds a fleet grows on (gcp, aws, hetzner).
- The pages, every one under the name the site syncs: the gateway API and every door in one table,
  tokens, codes, projections (PII masked when read), the line, a deploy that never cuts a call, the
  smallest app, people, the operator's doors, the box's settings and floor, settings, pipeline,
  providers, dev verbs, the console's reads, retrieval, scaling, charging for it, a box in
  production, from zero, prompt injection.
- A call's cost counts everything it billed: each leg on the phone network in minutes begun,
  priced by the longest prefix of its number (`CostRow.unit` takes `minutes`), and the tokens of
  the model that writes memory at hang-up. The box's prices ship as `infra/box/prices.csv`, the
  operator's to edit, applied with `pinecall-runtime providers prices`.
- A name per world: `PINECALL_DOMAIN` is production's and `PINECALL_SANDBOX_DOMAIN` the sandbox's,
  both one gateway. The name a request comes in by is its world: the console served at the
  sandbox's name is the sandbox's (the page carries `pinecall-world` and `pinecall-elsewhere`),
  `pinecall-env` may only agree (on a door and on the app and chat sockets alike),
  `/.well-known/pinecall` says `world` and `elsewhere`, a browser
  joins LiveKit at its own name, and a number imported in a world points its carrier at that
  world's name.

- The box's floor (`GET /v1/ops/events`) says each frame's world: `env` is the world of the call,
  `null` on an agent's own entries, so a reader serving one world keeps its frames and no other.
- Fixed: an org's feed (`GET /v1/events`) carried both worlds' calls; it carries the calls of the
  world the key acts in, and the org's agents' own entries, which serve both.
- A lexicon is one agent's: `GET`·`PUT /v1/agents/{slug}/lexicon` and `GET …/lexicon/history`
  replace `/v1/lexicon`, with the same bodies, keys, scopes and versions. Migration 0008 copies
  each org's lexicon to every agent the org has in that world, keeping its version numbers, so
  `GET /v1/calls/{call}/settings` still reads the words an older call ran on.
- A persona is one agent's: its doors are `/v1/agents/{slug}/personas[/{name}[/runs]]`, a name is
  unique within the agent, its runs are that agent's calls, and `/v1/evals/voice` refuses with
  `404` a name nobody wrote for the agent it calls. `/v1/personas` is gone, and so is a persona's
  `agents`: a row written for some agents becomes one copy each, one written for every agent one
  copy per agent the org has. Fixed by construction: the console and the CLI never sent `agents`,
  so every edit of a persona made it callable by every agent again.
- An agent's own judges: a question about its job the org writes (`GET /v1/agents/{slug}/judges`,
  `PUT`·`DELETE /v1/agents/{slug}/judges/{name}`, `pinecall judges`), asked of the judge model at
  hang-up beside the runtime's panel, on every call or only on simulated ones; its verdict is in
  `call.score` under its name. `POST /v1/evals/judge/{call}` asks them too.
- The org's own judges: `GET /v1/org/judges`, `PUT`·`DELETE /v1/org/judges/{name}`, questions
  asked of every agent's calls beside the panel and the agent's own; a name may not be the
  panel's, nor the org's and an agent's at once, and a judge without a question is refused.
- The judge's ceiling is the providers row's `judge.ceiling_usd`, applied: a model judge asked
  once the call's judging reached it is `skipped`, saying so. `PINECALL_JUDGE_CEILING_USD` is gone.
- Hosted apps, the record: `POST /v1/hosted/{name}/releases` keeps a project's sources (a gzipped
  tarball, read before it is kept: no link, no path out of the project, 10 MB) as the app's next
  release; the first makes the app, counted against the new `hosted_apps` quota, and mints a
  server's token for it, sealed. `GET /v1/hosted`, the releases and a release's source back,
  `DELETE /v1/hosted/{name}`. The org's secrets per world, sealed and never read back:
  `GET /v1/secrets`, `PUT`·`DELETE /v1/secrets/{name}`. Nothing builds or starts a release yet.
  Migration 0018.
- The runner's doors, for the process that will run hosted apps: a key scope `runner`
  (`pinecall-runtime keys runner <world>`), `POST /v1/runner/heartbeat` (every app of the world
  with the host its release runs under, whether it registered, and the runner's `live` and
  `failed` reports kept), a release's source and an app's environment (the org's secrets, its
  token, the world's address). `GET /v1/hosted` says `live_release` and `failed_why`.
  Migration 0019.
- The runner: `pinecall-runtime runner start`, the process that keeps a world's hosted apps
  running on a machine of their own (`infra/apps/`: podman, gVisor, an nftables fence, one runner
  unit per world). It installs a release inside gVisor, starts it read-only, capped and on a
  network of its own under the release's host name, reports it live once its agents register, and
  stops the release it replaced only then; one that does not install, exits or never registers is
  reported failed with its last lines, and the one before keeps serving. `PINECALL_RUNNER_KEY`,
  `PINECALL_RUNNER_ROOT`, `PINECALL_RUNNER_IMAGE`, `PINECALL_RUNNER_RUNTIME`.
- Each runner sees only its world's containers (`pinecall.world`), so two share a machine; an app's
  network is DNS-less and on a bridge of the runner's own (`pca…`), the only one the fence matches;
  the install's HOME is a scratch folder on disk. From a terminal: `pinecall deploy` and `pinecall secrets` (pinecall 0.9.10).
- A hosted app stopped and started (`POST /v1/hosted/{name}/stop`·`start`: its process drains, its
  releases and token stay), rolled back on the gateway (`POST …/rollback {release}`), and its logs
  read (`GET …/logs`: the runner sends its container's last 300 lines on the beat after they are
  asked for). The time each app served is counted per UTC day while a process of the org runs under
  one of its hosts: `GET /v1/hosted/usage`, and every org's at `GET /v1/ops/hosted-usage`, for a
  billing layer. Migration 0021.
- The hosting code reviewed, end to end. An org's secrets no longer ride podman's environment,
  which is root's (a secret named `LD_PRELOAD` was root's to run): the runner writes each host's
  environment to a tmpfs (`PINECALL_RUNNER_ENVIRONMENTS`, `/run/pinecall-runner/<world>`),
  mounted read-only into that container, and its shell reads it. A live host whose process exits
  is run again, its last lines kept as the app's logs; five exits in ten minutes and it is failed.
  Each app's steps run in a task of their own, planned from numbers alone (`runner/_plan.py`); a
  report is never lost to a beat that fails; release folders, environments and networks nothing
  uses are swept. A host's name is stamped with the org and the world, an app's name is 40
  characters at most, and the runner reads the gateway leniently (`live_host` is new): upgrade
  the runner first. The time served is counted in one statement a beat; an upload is capped while
  it streams. The operator's `pinecall-runtime` wrapper writes no bytecode, so a verb run as root
  no longer breaks the next release.
