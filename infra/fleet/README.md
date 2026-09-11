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
