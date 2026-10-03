# The fleet's clouds

A world's workers on machines of their own, as many as its calls need. There are two ways, and a
box picks the one its cloud has:

| | Google Cloud (Pinecall's) · AWS | any other cloud, a server of your own |
|---|---|---|
| makes the machines | a **managed instance group** (Terraform's `modules/fleet-gcp`) from the image family Packer builds · an **Auto Scaling group** (`modules/fleet-aws`) from the AMI it builds | the fleet loop, through a script with three verbs (`create`, `delete`, `list`) |
| grows the fleet | the group's autoscaler, on the fleet's calls the loop writes to Cloud Monitoring · target tracking on the same number in CloudWatch, its scale-in off | the loop |
| lets one go | the loop: cordons the quietest, waits for its calls, abandons it from the group and deletes it · terminates it out of the group, one less desired | the loop, the same way |
| a machine's credentials | read at its first boot from Secret Manager, as its own service account · from Secrets Manager, as its instance profile | spent at its first boot from a join token the loop made for it |
| heals one | the group, on the worker's health port · EC2's own checks | the loop deletes a machine silent for 5 min |

**Why the loop stays on Google Cloud too.** A group removing a machine itself gives it 90 seconds
to stop, and a call may last ten minutes; so the group only grows (`ONLY_SCALE_OUT`), and the loop
lets go of the one too many once it holds no call — LiveKit's own advice: scale out early, scale
in only once drained ([docs](https://docs.livekit.io/deploy/custom/deployments/)).

## On Google Cloud

Everything cloud-side is Terraform's (`infra/terraform/`): the fleet's subnet `10.100.0.0/24` and
its NAT (a machine has no public address), the rules that let it reach the box's LiveKit and
gateways and let Google's checkers reach its health port, the secrets and the world's worker
identity (`modules/secrets`), and the group with its template and autoscaler (`modules/fleet-gcp`).
The box's VM acts as `pinecall-fleet@…`, which may let go of the fleet's machines alone (a custom
role conditioned on their names) and write the metric.

```console
$ make image WORLD=production         # Packer: the worker image into pinecall-worker-production
$ make tf-apply ENV=production        # the template takes the family's newest image
```

From then on nothing is done by hand. The box runs `pinecall-fleet-loop@production`
(`install.sh` enables it from the box's metadata, which Terraform writes): every 15 s it reads the
roster, writes `custom.googleapis.com/pinecall/fleet_calls{fleet}` (the calls held), and lets go of
a machine that is one too many. The autoscaler keeps ⌈calls ÷ 19⌉ machines (32 seats × 0.6, so a
machine is asked for well before the last seat is taken), between `min` and `max` (`environments/production/main.tf`). A machine it
makes boots from the image, reads its world's fleet key, the LiveKit pair and the store's secret
from Secret Manager (`pinecall-runtime cell enroll`, run by `pinecall-join.service`), seals them
to its own vTPM, and its worker registers; the box's own workers of the world count in the
numbers and are never let go.

A new runtime is a new image: `make image`, and each machine the group makes from then on boots
it; the ones running keep theirs until the loop lets them go (the group never replaces a machine
that holds calls: `OPPORTUNISTIC`).

`infra/fleet/gcp-mig.py` is the loop's cloud on Google Cloud (`list`, `delete` = abandon then
delete, `measure <fleet> <calls>`; `create` is refused: the group grows). It takes the machine's
own token from the metadata server on the box, the gcloud login's elsewhere, and reads
`PINECALL_FLEET_PROJECT`, `_ZONE`, `_MIG` (`/etc/pinecall/fleet-loop-<world>.env`).

## On AWS

The same shape, written and validated (`make tf-check`), applied by no box yet: Pinecall's runs on
Google Cloud. `modules/secrets-aws` declares the five secrets in Secrets Manager under the same
names and a role and instance profile per world that reads its own fleet key and the three
shared; `modules/fleet-aws` is the launch template (the newest AMI named `pinecall-worker-<world>-*`,
IMDSv2 alone with the instance's tags readable, the instance id as the hostname, the tags
`pinecall-cloud=aws` and `pinecall-world`) and the Auto Scaling group with target tracking on
`IF(machines > 0, calls / machines, calls)` over `Pinecall/fleet_calls{fleet}` and its machines in
service, scale-in off, zone rebalancing suspended. `infra/terraform/examples/fleet-aws` is the root
a box on AWS copies into its environment.

```console
$ packer build -only=amazon-ebs.worker -var world=production -var region=us-east-1 -var seats=32 \
    -var box_address=<the box> -var package=dist/pinecall-<v>.whl -var settings=<worker-settings tar> infra/packer
$ PINECALL_FLEET_ASG=pinecall-workers-production pinecall-runtime fleet loop \
    --cloud infra/fleet/aws-asg.py --fleet pinecall --seats 32 --min 0 --max 10 --grow-at-most 0
```

A machine it makes reads its credentials with `cell enroll` through the aws CLI the image
carries, as its instance profile; the box writes them with `cell publish-secrets` on a box on AWS.

## Anywhere else

`pinecall-runtime fleet loop --cloud infra/fleet/<cloud> --seats <n> --fleet <fleet>` keeps a
fleet at its target ([docs/scaling.md](../../docs/scaling.md)). A cloud is one executable with
three verbs, and nothing cloud-specific lives anywhere else:

```
<script> create <name>    a machine from the worker image, labelled the fleet's, named <name>; its
                          environment carries PINECALL_JOIN_TOKEN and PINECALL_JOIN_URL, which
                          `first-boot <name>` turns into the machine's user-data (cloud-config)
<script> delete <name>    that machine gone
<script> list             one line per fleet machine: name<TAB>created (ISO 8601)
```

| script | needs | set |
|---|---|---|
| `first-boot` | — | shared by the clouds: `first-boot <name>` prints the cloud-config a `create` hands over |
| `gcp` | `gcloud` | a project with no group: `PINECALL_FLEET_PROJECT` (the gcloud default), `_ZONE`, `_IMAGE` (a machine image), `_TYPE`, `_LABEL`, `_SUBNET` |
| `hetzner` | `hcloud`, `jq` | `PINECALL_FLEET_IMAGE` (required), `_TYPE`, `_LOCATION`, `_LABEL` |
| `gcp-mig.py` | Python 3 | the Google Cloud group above, with `--grow-at-most 0` |
| `aws-asg.py` | Python 3, `aws` | the AWS group below, with `--grow-at-most 0`: `PINECALL_FLEET_ASG` |

A cloud of your own is a script with the same three verbs: `--cloud ./yours`.

The image is a worker machine prepared once with `pinecall-runtime cell image-worker` (the box's
`cell worker-settings`, no secret) and frozen **with no credential on it**. A machine made from it
takes the name it was given as its hostname and, at its first boot, spends the join token the loop
made for it (`POST /v1/fleet/join`, `cell enroll`) for a fleet key of its own, the LiveKit pair and
the store's secret, sealed to that machine alone. The token is one key of the box's `api_keys`:
scope `join`, named for the machine, ten minutes, revoked as it is spent; `delete` revokes the
machine's fleet key with it (`DELETE /v1/ops/fleet/{worker}/keys`). The loop runs wherever the
cloud's CLI is signed in and the box's operator key is in its environment.

```console
box$ sudo pinecall-runtime cell allow-worker <the fleet's subnet>
$ ssh box 'sudo pinecall-runtime cell worker-settings sandbox' |
    ssh base 'sudo uvx --from pinecall==<the box version> pinecall-runtime cell image-worker <box address> sandbox --calls 32'
$ # stop the base machine and freeze it with the cloud's own command; then:
$ export PINECALL_OPS_KEY="$(ssh box 'sudo systemd-creds decrypt --name=PINECALL_OPS_KEY /etc/credstore.encrypted/PINECALL_OPS_KEY -')"
$ PINECALL_GATEWAY_URL=https://sandbox.example.com pinecall-runtime fleet loop \
    --cloud infra/fleet/<cloud> --seats 32 --fleet pinecall-sandbox --min 3 --max 6
```

`--min` counts every worker the fleet has, the box's own among them (two per world). Measured on
Google Cloud on 2026-10-02 with the join path (the sandbox fleet, a laptop's loop, no call): a
machine `accepting` 99 s after the loop asked for it, deleted 86 s after its cordon with its key
revoked.
