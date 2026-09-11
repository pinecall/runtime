# The fleet's clouds

One script per provider, three verbs each, and this is **all** the cloud-specific code there is.
The loop (`pinecall-runtime fleet loop --cloud <name>`) never imports a vendor SDK: it runs the
script and reads its lines.

```
<script> create <name>     a machine that boots from the worker image and dials the hub by itself
<script> delete <name>     gone
<script> list              one line per fleet machine:  name<TAB>created-at   (ISO 8601)
```

| script | needs | image |
|---|---|---|
| `gcp` | `gcloud` signed in; `PINECALL_FLEET_PROJECT`, `_ZONE`, `_IMAGE`, `_TYPE` (defaults inside) | a **machine image** of a deployed worker |
| `aws` | `aws` signed in; `PINECALL_FLEET_AMI`, `_SUBNET`, `_SG` (required), `_TYPE` | an **AMI** of a deployed worker |
| `hetzner` | `hcloud` with `HCLOUD_TOKEN`; `PINECALL_FLEET_IMAGE` (required), `_TYPE`, `_LOCATION` | a **snapshot** of a deployed worker |

**The image is a worker that was deployed once and frozen.** Stand one worker up by hand
(`infra/box/README.md`, "Roles, and a second box"), `make deploy` to it, watch it take a call,
then take the image. A machine made from it boots with the code, the units, the encrypted
credentials and `box.env` of that worker, and its hostname is the name the loop gave it — which is
the name it heartbeats to the hub under, so a seat on the hub and a machine at the cloud are one
word. Nothing is copied at boot and nothing has to be.

Every machine the loop makes carries the fleet's label (`pinecall-fleet=1`; a tag on AWS), and
`list` returns **only** those: the loop never cordons or deletes a machine it does not list. A
worker you stood up by hand counts in the numbers — its seats, its calls — and is never let go;
label it and it joins the fleet the loop sizes.

A cloud of your own is forty lines: the same three verbs, the same `list` line, and
`--cloud ./path/to/it`.

## Giving the hub the right to grow the fleet, on GCP

The permission lives on the **VM**, not in a file: a service account attached to the hub, so
`gcloud` on the box reads the metadata server and never logs in. Once, from a laptop:

```bash
P=<project>; SA=pinecall-fleet@$P.iam.gserviceaccount.com
gcloud compute addresses create pinecall-box-ip --addresses <the hub's IP> --region <region>   # first: the IP outlives a stop
gcloud iam service-accounts create pinecall-fleet --display-name "Pinecall fleet loop"
gcloud projects add-iam-policy-binding $P --member serviceAccount:$SA --role roles/compute.instanceAdmin.v1
gcloud iam service-accounts add-iam-policy-binding <the workers' service account> --member serviceAccount:$SA --role roles/iam.serviceAccountUser
gcloud compute instances stop <hub>; gcloud compute instances set-service-account <hub> --service-account $SA --scopes cloud-platform; gcloud compute instances start <hub>
```

The stop is the one disruptive step — a service account can only be attached to a stopped VM —
so reserve the address first, and do it between two calls. Then in `/etc/pinecall/box.env`:

```
PINECALL_FLEET_CLOUD=gcp
PINECALL_FLEET_SEATS=4
PINECALL_FLEET_PROJECT=<project>
```

and `make deploy`: the manifest enables `pinecall-fleet.service`, and `journalctl -u
pinecall-fleet -f` is the loop, one line a tick. `instanceAdmin.v1` is create, delete, list and
label instances and use a machine image; `serviceAccountUser` is the right to start a machine
that runs as the workers' own account. Nothing else.
