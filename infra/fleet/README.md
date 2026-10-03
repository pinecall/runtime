# The fleet's clouds

A world's workers on machines of their own, as many as its calls need. **The fleet loop sizes the
fleet, everywhere, and nothing else does**: it grows it when the fleet is busy over its target and
lets a machine go only once it is cordoned and its calls ended. What changes with the cloud is who
makes a machine it asked for, and how that machine gets its credentials:

| | Google Cloud (Pinecall's) · AWS | any other cloud, a server of your own |
|---|---|---|
| makes the machines | a **managed instance group** (Terraform's `modules/fleet-gcp`) from the image family Packer builds · an **Auto Scaling group** (`modules/fleet-aws`) from the AMI it builds; neither has an autoscaler or a scaling policy | the cloud's own API, through the script |
| the loop's script | `gcp-mig.py`: `create` makes a machine of that name in the group, `delete` deletes it out of the group · `aws-asg.py`: `create` raises the desired capacity by one, `delete` terminates it out of the group | three verbs (`create`, `delete`, `list`) |
| a machine's credentials | read at its first boot from Secret Manager, as its own service account · from Secrets Manager, as its instance profile | spent at its first boot from a join token the loop made for it |
| heals one | the group, on the worker's health port · EC2's own checks | the loop deletes a machine silent for 5 min |

**Why one controller.** Until 2026-10-03 the group on Google Cloud also had an autoscaler, grown on
the fleet's calls the loop wrote to Cloud Monitoring, while the loop let go of the one too many. The
two rules disagreed: the autoscaler counted the calls the box's own workers held and asked for a
machine at the first call; the loop saw the box's seats free and let it go; the autoscaler, holding
the peak of its last ten minutes, made another. Production made and deleted five machines in forty
minutes with one to three calls up, all of which the box's four seats held. Now a group never sizes itself: a group removing a machine
itself gives it 90 seconds, and a call may last ten minutes — LiveKit's own advice is to scale in
only once drained ([docs](https://docs.livekit.io/deploy/custom/deployments/)), and only the loop
knows when a worker drained.

## On Google Cloud

Everything cloud-side is Terraform's (`infra/terraform/`): the fleet's subnet `10.100.0.0/24` and
its NAT (a machine has no public address), the rules that let it reach the box's LiveKit and
gateways and let Google's checkers reach its health port, the secrets and the world's worker
identity (`modules/secrets`), and the group with its template (`modules/fleet-gcp`). The box's VM
acts as `pinecall-fleet@…`, which may make and delete the fleet's machines through their group
alone (a custom role conditioned on their names).

```console
$ make image WORLD=production         # Packer: the worker image into pinecall-worker-production
$ make tf-apply ENV=production        # the template takes the family's newest image
```

From then on nothing is done by hand. The box runs `pinecall-fleet-loop@production`
(`install.sh` enables it from the box's metadata, which Terraform writes, and a release stops and
starts it around the package): every 15 s it reads the roster and, by its line
([docs/scaling.md](../../docs/scaling.md)), makes a machine in the group when the fleet is busy
over 0.6 of its seats, or cordons the quietest when it would stay under 0.45 without it and deletes
it once drained, between 0 and `PINECALL_FLEET_MAX` machines. The box's own workers of the world
count in the numbers and are never let go, so a call or two on the box makes no machine. A machine
the group makes boots from the image, reads its world's fleet key, the LiveKit pair and the store's
secret from Secret Manager (`pinecall-runtime cell enroll`, run by `pinecall-join.service`), seals
them to its own vTPM, and its worker registers; the loop counts it as 32 seats from the moment it
asked, so it asks once and waits.

A new runtime is a new image: `make image`, and each machine the group makes from then on boots
it; the ones running keep theirs until the loop lets them go (the group never replaces a machine
that holds calls: `OPPORTUNISTIC`).

`infra/fleet/gcp-mig.py` is the loop's cloud on Google Cloud (`create <name>` = the group's
`createInstances`, `delete <name>` = its `deleteInstances`, each back once the machine exists or is
gone; `list`). It takes the machine's own token from the metadata server on the box, the gcloud
login's elsewhere, and reads `PINECALL_FLEET_PROJECT`, `_ZONE`, `_MIG`
(`/etc/pinecall/fleet-loop-<world>.env`).

## On AWS

The same shape, written and validated (`make tf-check`), applied by no box yet: Pinecall's runs on
Google Cloud. `modules/secrets-aws` declares the five secrets in Secrets Manager under the same
names and a role and instance profile per world that reads its own fleet key and the three
shared; `modules/fleet-aws` is the launch template (the newest AMI named `pinecall-worker-<world>-*`,
IMDSv2 alone with the instance's tags readable, the instance id as the hostname, the tags
`pinecall-cloud=aws` and `pinecall-world`) and the Auto Scaling group with no scaling policy, zone
rebalancing suspended. `infra/terraform/examples/fleet-aws` is the root a box on AWS copies into
its environment. A machine EC2 makes is named by its instance id, which is its hostname and so its
worker's name: `create` keeps no name the loop asked for.

```console
$ packer build -only=amazon-ebs.worker -var world=production -var region=us-east-1 -var seats=32 \
    -var box_address=<the box> -var package=dist/pinecall-<v>.whl -var settings=<worker-settings tar> infra/packer
$ PINECALL_FLEET_ASG=pinecall-workers-production pinecall-runtime fleet loop \
    --cloud infra/fleet/aws-asg.py --fleet pinecall --seats 32 --min 0 --max 10
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
| `gcp-mig.py` | Python 3 | the Google Cloud group above: `PINECALL_FLEET_PROJECT`, `_ZONE`, `_MIG` |
| `aws-asg.py` | Python 3, `aws` | the AWS group above: `PINECALL_FLEET_ASG` |

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
