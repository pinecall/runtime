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

```console
$ gcloud compute instances create pinecall-apps-1 --machine-type e2-standard-4 \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size 50GB --no-service-account --no-scopes
$ gcloud compute scp --recurse infra/apps pinecall-apps-1:/tmp/apps
$ gcloud compute ssh pinecall-apps-1 --command 'sudo sh /tmp/apps/install.sh "pinecall==<version>" production'
```

An E2 is enough: gVisor's default platform needs no KVM, and GCP refuses nested virtualisation on
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
$ ssh example-box 'sudo pinecall-runtime keys runner production' \
  | gcloud compute ssh pinecall-apps-1 --command \
    'sudo systemd-creds encrypt --name=PINECALL_RUNNER_KEY - /etc/pinecall/runner/production.credstore/PINECALL_RUNNER_KEY'
$ gcloud compute ssh pinecall-apps-1 --command 'sudo systemctl enable --now pinecall-runner@production'
```

## What the runner does, every five seconds

It tells the gateway what happened and is told every app of its world with a release. For each:
it installs a release it has not installed (the dependencies, inside gVisor, 5 minutes and 1 GB
at most), starts it under the release's **host** name with the org's secrets and the app's token
in its environment, and reports it `live` once the gateway sees an app socket say that host. The
release it replaces keeps serving until then, and is stopped with SIGTERM and 45 seconds to
drain. A release that does not install, exits, or registers nothing within two minutes is
reported `failed` with its last lines, and the one before it keeps serving. An app dropped has its
container stopped.

```console
$ sudo podman ps --filter label=pinecall.app                # every app's container
$ sudo podman logs -f <host>                                # one app's output
$ journalctl -u pinecall-runner@production -f               # the runner
```
