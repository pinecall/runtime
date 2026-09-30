# Hosted apps: the box runs the org's agent

A tenant's agent is a process the tenant runs ([the-smallest-app.md](the-smallest-app.md)). A
**hosted app** is the same process run by the box instead: the org uploads the project's sources,
and the box keeps them as a **release**. Every door here takes a key that opens `app`, and acts in
the world the request names: an app hosted in production is not hosted in the sandbox.

**What exists today is the record and the runner's doors**: the app, its token, its releases, the
org's secrets, and what a runner asks the gateway. The runner itself, the process that builds a
release and starts it, is not written yet, so an upload is kept and nothing runs it.

## An app and its releases

| door | what |
|---|---|
| `POST /v1/hosted/{name}/releases[?note=]` | the body is the project as a gzipped tarball (`application/gzip`, no multipart); answers the release: `{name, release, sha256, bytes, author, note, created_at}` |
| `GET /v1/hosted` | `{apps: [{name, release, live_release, failed_why, created_by, created_at}]}`, by name. `release` is the newest, `null` before the first; `live_release` the one serving the app now, `null` while none is; `failed_why` why the newest did not build or start, `null` when it did or is on its way |
| `GET /v1/hosted/{name}/releases` | `{releases: [...]}`, newest first |
| `GET /v1/hosted/{name}/releases/{release}/source` | the tarball as it was uploaded |
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
| `POST /v1/runner/heartbeat {runner, reports: [{org, name, host, state, why}]}` | keeps the reports, and answers every app of the key's world that has a release: `{apps: [{org, name, release, sha256, host, registered, failed}]}` |
| `GET /v1/runner/apps/{org}/{name}/releases/{release}/source` | the tarball, of any org in the key's world |
| `GET /v1/runner/apps/{org}/{name}/environment` | `{environment: {…}}`: the org's secrets in that world opened, `PINECALL_KEY` (the app's token) and `PINECALL_URL` (the box's address for that world; `503` on a box with no name) |

**A host is one release under one set of secrets.** `host` is the name the release's process is
to run under, `<name>-r<release>-<eight hex>`; the hex is of the release and of the org's secrets
as they are, so an upload and a changed secret are both a new host. The SDK says the machine it
runs on when it registers, so `registered` is true once an app socket of the org says it runs on
that host: the release is serving.

A report says a host went `live` or `failed` (with `why`, 2 000 characters at most). It is kept
only while that host is still the one wanted. A host reported failed comes back `failed: true`, and
the runner leaves it alone until a release or a secret makes the next host.
