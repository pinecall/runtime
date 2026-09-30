# Hosted apps: the box runs the org's agent

A tenant's agent is a process the tenant runs ([the-smallest-app.md](the-smallest-app.md)). A
**hosted app** is the same process run by the box instead: the org uploads the project's sources,
and the box keeps them as a **release**. Every door here takes a key that opens `app`, and acts in
the world the request names: an app hosted in production is not hosted in the sandbox.

The box's **runner** of that world installs each release and starts it, one gVisor container
each, on a machine that is not the box ([../../infra/apps/README.md](../../infra/apps/README.md));
`GET /v1/hosted` says which release serves and why the newest failed. What the process is started
with is the org's secrets, its token and the world's address, and the command is always
`pinecall start` (`--prod` in production): a hosted project is a Node project with `pinecall` in
its dependencies.

From a terminal, the doors are two verbs of the `pinecall` CLI (0.9.10 and later):
`pinecall deploy` packs the folder (never `node_modules` or a `.env`), uploads it and follows it
until it is live or failed, with `list`, `releases`, `rollback <n>` and `rm`; `pinecall secrets`
sets, lists and drops the org's secrets. Their page is the agents repo's `docs/the-cli.md`.

## An app and its releases

| door | what |
|---|---|
| `POST /v1/hosted/{name}/releases[?note=]` | the body is the project as a gzipped tarball (`application/gzip`, no multipart); answers the release: `{name, release, sha256, bytes, author, note, created_at}` |
| `GET /v1/hosted` | `{apps: [{name, release, live_release, failed_why, stopped, created_by, created_at}]}`, by name. `release` is the newest, `null` before the first; `live_release` the one serving the app now, `null` while none is; `failed_why` why the newest did not build or start, `null` when it did or is on its way |
| `GET /v1/hosted/{name}/releases` | `{releases: [...]}`, newest first |
| `GET /v1/hosted/{name}/releases/{release}/source` | the tarball as it was uploaded |
| `POST /v1/hosted/{name}/rollback {release}` | release n's sources kept again as the next release, noted `rollback to release n`; answers the new release |
| `POST /v1/hosted/{name}/stop` · `…/start` | `204`. Stopped, the runner is no longer told to run it: its process drains, its releases and token stay; started, its newest release runs again |
| `GET /v1/hosted/{name}/logs` | `{name, host, lines, at}`: the last lines of the app's process as the runner last read them (64 KB at most). Asking is what makes the runner send them again, on its next beat — a first ask answers `lines: ""` and `at: null`, and a second a few seconds later has them |
| `GET /v1/hosted/usage[?month=YYYY-MM]` | `{since, until, rows: [{org, env, name, day, seconds}]}`: the time each app served per UTC day of one month, this one by default |
| `DELETE /v1/hosted/{name}` | `204`: the app and its releases go, and its token is revoked |

`{name}` is a slug. **The first upload makes the app**: it is counted against the org's
`hosted_apps` quota in that world ([../limits.md](../limits.md); `429` at the limit, and a quota
of zero hosts nothing), and a server's token is minted for it — the org's, in that world, labelled
`hosted app <name>`, listed in `GET /v1/keys` like any other and kept sealed under the vault key
for the process the box will start. No door answers it. Later uploads are releases 2, 3, …: a
release is never edited, and its number never reused.

An upload is read before it is kept, and refused with a `400` that says why:

- over 10 MB packed, over 100 MB unpacked, or more than 5 000 files (leave `node_modules` and
  build output out: the box installs the dependencies itself);
- anything but a gzipped tarball;
- a path that leaves the project's folder (`../`, an absolute path), or a member that is a link
  or a device. A release holds files and folders only.

## The time an app served

What an org is billed for is the time its apps served: counted on every beat of the runner while a
process of the org runs under one of the app's hosts, whichever release, up to 30 seconds a beat,
on a row per app and UTC day that outlives the app. A stopped app counts nothing. The operator
reads every org's with `GET /v1/ops/hosted-usage[?month=YYYY-MM]`, the same shape.

## The org's secrets

What the org's hosted apps are started with, as environment variables, per world.

| door | what |
|---|---|
| `GET /v1/secrets` | `{secrets: [{name, set_by, set_at}]}`, by name. Never a value |
| `PUT /v1/secrets/{name} {value}` | keeps it sealed under the vault key, replacing the value it had; answers the list |
| `DELETE /v1/secrets/{name}` | forgets it; answers the list, `404` for a name nobody set |

A name is an environment variable's (`CRM_TOKEN`: capitals, digits, underscores), and never one
starting with `PINECALL_`, which the box sets itself. A value is 16 KB at most. **No door reads a
value back**: a secret is set, replaced or dropped.

## The runner's doors

The **runner** is the box's own process that runs every org's hosted apps in one world. Its key
opens `runner`, a scope no org's key and no person's role holds: it is minted on the box
(`pinecall-runtime keys runner production|sandbox`), in the box's own org, like a fleet's.

| door | what |
|---|---|
| `POST /v1/runner/heartbeat {runner, reports: [{org, name, host, state, why}], logs: [{org, name, host, lines}]}` | keeps the reports and the logs, counts the time each serving app served, and answers the key's world and every app of it that has a release and is not stopped: `{world, apps: [{org, name, release, sha256, host, registered, failed, logs_wanted}]}` |
| `GET /v1/runner/apps/{org}/{name}/releases/{release}/source` | the tarball, of any org in the key's world |
| `GET /v1/runner/apps/{org}/{name}/environment` | `{environment: {…}}`: the org's secrets in that world opened, `PINECALL_KEY` (the app's token) and `PINECALL_URL` (the box's address for that world; `503` on a box with no name) |

**A host is one release under one set of secrets.** `host` is the name the release's process is
to run under, `<name>-r<release>-<eight hex>`; the hex is of the release and of the org's secrets
as they are, so an upload and a changed secret are both a new host. The SDK says the machine it
runs on when it registers, so `registered` is true once an app socket of the org says it runs on
that host: the release is serving.

A report says a host went `live` or `failed` (with `why`, 2 000 characters at most). It is kept
only while that host is still the one wanted. A host reported failed comes back `failed: true`, and
the runner leaves it alone until a release or a secret makes the next host. `logs_wanted` is true
for a minute after somebody asked for the app's logs: the runner reads its container's last 300
lines and sends them with its next beat.
