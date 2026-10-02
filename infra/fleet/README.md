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
