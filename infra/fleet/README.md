# The fleet's clouds

`pinecall-runtime fleet loop --cloud infra/fleet/<cloud> --seats <n>` keeps a fleet at its target
([docs/scaling.md](../../docs/scaling.md)). A cloud is one executable with three verbs, and nothing
cloud-specific lives anywhere else:

```
<script> create <name>    a machine from the worker image, labelled the fleet's, named <name>
<script> delete <name>    that machine gone
<script> list             one line per fleet machine: name<TAB>created (ISO 8601)
```

The image is a worker that was deployed once and frozen: it boots with the wheel, the units, the
fleet's key and `fleets/<world>.env`, takes the name it was given as its hostname, and heartbeats
to the gateway by itself. Nothing is copied at boot. The loop runs wherever the cloud's CLI is
signed in: a laptop, or the box itself.

| script | needs | set |
|---|---|---|
| `gcp` | `gcloud` | `PINECALL_FLEET_PROJECT` (the gcloud default), `_ZONE`, `_IMAGE` (a machine image), `_TYPE`, `_LABEL`, `_SUBNET` (the subnet the box lets in) |
| `aws` | `aws` | `PINECALL_FLEET_AMI`, `_SUBNET`, `_SG` (required), `_TYPE`, `_TAG` |
| `hetzner` | `hcloud`, `jq` | `PINECALL_FLEET_IMAGE` (required), `_TYPE`, `_LOCATION`, `_LABEL` |

A cloud of your own is a script with the same three verbs: `--cloud ./yours`.

## An image, and a first run (Google Cloud, done this way on 2026-10-02)

The machines the loop makes need to reach the box, and their addresses are new each time: give the
fleet a subnet of its own and let that range in once. On the box's network:

```console
$ gcloud compute networks subnets create pinecall-fleet --network default --region us-central1 \
    --range 10.100.0.0/24        # outside 10.128.0.0/9 on an auto-mode network
$ gcloud compute firewall-rules create pinecall-fleet-to-box --network default \
    --source-ranges 10.100.0.0/24 --target-tags <the box's tag> --allow tcp:7880,tcp:8088
box$ sudo /opt/pinecall/infra/cell/primary.sh allow-worker 10.100.0.0/24
```

The image is one worker machine, joined and frozen. Make it the machine type the fleet will use:
`worker.sh join` writes its seats as four per vCPU, and every copy keeps that number.

```console
$ gcloud compute instances create pinecall-worker-base --subnet pinecall-fleet \
    --machine-type e2-standard-8 --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --metadata-from-file user-data=infra/box/cloud-init.yaml     # the box's, your ssh key in it
$ ssh box 'sudo tar -C /opt/pinecall -c infra' | ssh worker-base 'sudo mkdir -p /opt/pinecall && sudo tar -C /opt/pinecall -x'
$ ssh box 'sudo /opt/pinecall/infra/cell/primary.sh worker-credentials sandbox' |
    ssh worker-base 'sudo /opt/pinecall/infra/cell/worker.sh join <box address> pinecall==0.1.4 sandbox'
box$ sudo pinecall-runtime fleet list            # pinecall-worker-base accepting, 32 seats
$ gcloud compute instances stop pinecall-worker-base
$ gcloud compute machine-images create pinecall-worker-sandbox-014 --source-instance pinecall-worker-base
$ gcloud compute instances delete pinecall-worker-base
```

Then the loop, wherever gcloud is signed in, with the box's operator key in its environment (read
from the box into the variable, never typed):

```console
$ export PINECALL_OPS_KEY="$(ssh box 'sudo systemd-creds decrypt --name=PINECALL_OPS_KEY /etc/credstore.encrypted/PINECALL_OPS_KEY -')"
$ PINECALL_GATEWAY_URL=https://sandbox.example.com PINECALL_FLEET_SUBNET=pinecall-fleet \
  PINECALL_FLEET_TYPE=e2-standard-8 PINECALL_FLEET_IMAGE=pinecall-worker-sandbox-014 \
  pinecall-runtime fleet loop --cloud infra/fleet/gcp --seats 32 --fleet pinecall-sandbox --min 3 --max 3
```

`--min` counts every worker the fleet has, the box's own among them (two per world): `--min 3` on a
box is one machine. The first run, with no call: the machine was created, booted, opened its
sealed credentials and was `accepting` 137 s after the loop started; run again with `--min 2`, the
loop cordoned it (never the box's workers, which no cloud lists) and deleted it 71 s later.

What the image carries: the fleet's key, the LiveKit pair and the object store's secret, sealed with
the host's key that is in the image too. Anyone who can read the image can open them; keep it in the
box's project, readable by its operators alone. A new wheel is a new image (`docs/scaling.md`, "Deploys
drain, cordons shrink").
