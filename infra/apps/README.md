# infra/apps — the machine that runs the orgs' hosted apps

An org uploads a project (`POST /v1/hosted/{name}/releases`, [../../docs/protocol/hosting.md](../../docs/protocol/hosting.md));
the **runner** of that world, on this machine, installs it and starts it in a container of its
own, under gVisor. Nothing here holds the box's database or its vault key: the runner reaches
the box's gateway over HTTPS with its world's runner key, and that is all it has.

**Never the box.** The box holds Postgres, the vault key and LiveKit's keys; this is another VM,
with a service account that has no roles.

| file | what |
|---|---|
| `install.sh` | Ubuntu 24.04 made this machine: podman, gVisor (`runsc`), the fence, the runtime, a runner per world |
| `pinecall-runner@.service` | the runner of world `%i`, as root (podman's `--userns=auto` needs it; the containers never run as root) |
| `fence.nft` | what a container may reach: the internet, and nothing of ours |
| `runsc.conf` | gVisor, known to podman by name |

## A machine, from nothing

The machine is Terraform's (`module "apps"` in `infra/terraform/environments/production/main.tf`:
an `e2-medium`, 40 GB, **no service account**), like every other one; what runs on it is this
folder's:

```console
$ make tf-apply ENV=production       # the machine, if it is not there
$ gcloud compute scp --recurse infra/apps pinecall-apps-1:/tmp/apps
$ gcloud compute ssh pinecall-apps-1 --command 'sudo sh /tmp/apps/install.sh "pinecall==<version>" production'
```

A bigger one is `machine_type` in that module and an apply (which stops it for the change only
with `allow_stopping_for_update` set for that apply). An E2 is enough: gVisor's default platform needs no KVM, and GCP refuses nested virtualisation on
E2. Measured on one (`internal-docs/DEPLOY.md`, D0): an idle agent takes 72 MB, so a 4-vCPU,
16 GB machine holds 80–100 apps.

## The gateway and the key

`/etc/pinecall/runner/<world>.env` says which gateway the runner knocks, the box's name for that
world:

```
PINECALL_GATEWAY_URL=https://box.pinecall.io
```

The key is minted **on the box**, printed once into this machine's credstore and never shown:

```console
$ ssh pinecall-runtime-v2 'sudo pinecall-runtime keys runner production' \
  | gcloud compute ssh pinecall-apps-1 --command \
    'sudo systemd-creds encrypt --name=PINECALL_RUNNER_KEY - /etc/pinecall/runner/production.credstore/PINECALL_RUNNER_KEY'
$ gcloud compute ssh pinecall-apps-1 --command 'sudo systemctl enable --now pinecall-runner@production'
```

## What an org does

Nothing on this machine: `pinecall deploy` in the project's folder (`--prod` for production), and
`pinecall secrets set <NAME>` for what its code reads from the environment. The first upload makes
the app, within the org's `hosted_apps` quota; the runner of that world picks it up on its next beat.

## One machine, both worlds

A runner per world may share a machine, as the first one does: each container carries
`pinecall.world`, and a runner lists and touches only its own world's. Their keys, env files and
state folders are per world (`/etc/pinecall/runner/<world>.*`, `/var/lib/pinecall/runner/<world>`).

## The machines

`pinecall-apps-1` (e2-medium, 2 vCPU, 4 GB, `us-central1-c`, no service account) runs both worlds'
runners since 2026-09-30: at 72 MB an idle app it holds about forty. The old box ran them for the
first day; its runners are disabled and their keys revoked.

## A machine that is also a box

The first day's apps machine was the old box, which runs its own containers on a podman bridge and has
its own fence. What that asked of the runner, and still asks of whoever touches the machine:

- the runner's venv is `/opt/pinecall-runner/venv`, never the box's `/opt/pinecall/venv`;
- the fence matches only the runner's bridges (`pca…`), never `podman*`;
- the app networks carry no podman DNS (`--disable-dns`): the box's fence closes the host to them;
- a box deploy that rewrites `/etc/nftables.conf` drops the `include` of `pinecall-apps.nft`:
  run `install.sh` again after one.

## What the runner does, every five seconds

It tells the gateway what happened and is told every app of its world with a release. What to do
to each app is decided from numbers alone (`pinecall/runner/_plan.py`), and each app's steps run
in a task of their own, so a slow install never holds another app back (two installs at most at
once). For each app it installs a release it has not installed (the dependencies, inside gVisor,
5 minutes and 1 GB at most; marked `r<n>.installed` beside the folder, never in it), writes its
environment — the org's secrets, the app's token, the gateway's address — to
`/run/pinecall-runner/<world>/<host>/env`, a tmpfs the unit mounts, and starts it under the
release's **host** name with that file mounted read-only: podman never sees a secret. It reports
it `live` once the gateway sees an app socket say that host. The release it replaces keeps serving
until then, and is stopped with SIGTERM and 45 seconds to drain. A release that does not install,
exits, or registers nothing within two minutes is reported `failed` with its last lines, and the
one before it keeps serving. A live host whose process exits is run again, its last lines sent as
the app's logs; five exits in ten minutes and it is reported failed. An app dropped has its
container stopped. What nothing uses any more goes: a release's folder, a host's environment, and
the world's networks no container is on.

## Upgrading

The runner reads what the gateway answers leniently, so a gateway may add a field before its
runners know it, and never the other way round: **upgrade the runner first**, then deploy the box.

```console
$ gcloud compute scp dist/pinecall-<version>-py3-none-any.whl infra/apps/pinecall-runner@.service pinecall-apps-1:/tmp/
$ gcloud compute ssh pinecall-apps-1 --command 'sudo /opt/pinecall-runner/venv/bin/pip install --force-reinstall --no-deps /tmp/pinecall-*.whl \
    && sudo cp /tmp/pinecall-runner@.service /etc/systemd/system/ && sudo systemctl daemon-reload \
    && sudo systemctl restart pinecall-runner@production pinecall-runner@sandbox'
```

The apps keep running while their runner restarts: they are podman's, not the runner's.

```console
$ sudo podman ps --filter label=pinecall.app                # every app's container
$ sudo podman logs -f <host>                                # one app's output
$ journalctl -u pinecall-runner@production -f               # the runner
```
