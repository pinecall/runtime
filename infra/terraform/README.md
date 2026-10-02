# Terraform: the cloud around the box

What runs **on** a machine is the package's (`box up`, `install.sh`, the `cell` verbs). What makes
the machines and the cloud around them is here: the network's pieces, the firewall, the addresses,
the VMs, the buckets, the IAM users, the secrets, the fleet. **Nothing cloud-side is made or
changed by hand**: a resource that is not here does not exist, and a `plan` that is not empty says
the cloud drifted from the repository. A box on a home machine or another provider uses none of
this: `box up` and `cell join-worker` are the whole of it (`docs/a-box-in-production.md`).

```
bootstrap/                the state bucket alone, applied once with local state
modules/<name>/           what a thing is (one module per concern)
environments/production/  the root module of production: the modules, its variables, its imports
environments/lab/         the voice lab: three machines and their firewall rule, made and destroyed by infra/lab/measure.py
examples/fleet-aws/       a fleet on AWS, the root a box on AWS copies: validated, applied by nobody
```

Google's guidance, followed ([root modules](https://docs.cloud.google.com/docs/terraform/best-practices/root-modules)):
one root module per environment, each with `backend.tf` (its state in the bucket below, under its
own prefix), `versions.tf` (providers pinned to a minor), `providers.tf`, `variables.tf`,
`terraform.tfvars` (committed: it holds no secret), `.terraform.lock.hcl` committed; a few dozen
resources a state; the default workspace only.

## What is here

| module | what it holds | production's |
|---|---|---|
| `network` | the fleet's subnet (private access to Google's APIs on) and its NAT, the box's four firewall rules by its tag and the workers' health rule, its static address; the VPC itself is the project's `default`, shared with other machines, and only read | `pinecall-fleet` 10.100.0.0/24; `pinecall-runtime-web`, `-sip` (Twilio's edges alone), `-media`, `pinecall-fleet-to-box`; `pinecall-runtime-ip` |
| `machine` | one VM from cloud-init, shielded with its vTPM, live-migrated; a machine made already is never replaced for its image or its first boot's data | `pinecall-runtime` (the box), `example-replica` |
| `store` | the two private buckets, the box's IAM user and its policy, the alerts' user, SES | `pinecall-box-backups-…` (35-day lifecycle), `pinecall-box-recordings-…` (no lifecycle) |
| `dns` | the names that point at the box, in Route 53; the zone's other records are other repositories' | `box`, `sandbox`, `notify`, `billing` .pinecall.io |
| `fleet-gcp` | a world's worker template (the image family `pinecall-worker-<world>`, no public address, its identity, `pinecall-cloud`/`-world` metadata), the managed group healed on the health port, its autoscaler `ONLY_SCALE_OUT` on the fleet's calls | `pinecall-workers-production`, 0–10 machines, 19 calls each |
| `fleet-aws` | the same on AWS: a launch template from the AMI Packer builds, an Auto Scaling group grown by target tracking on `Pinecall/fleet_calls`, its scale-in off | none: validated in `examples/fleet-aws` |
| `secrets-aws` | the five secrets in Secrets Manager, a role and instance profile per world reading its own | none |
| `secrets` | the five secrets a fleet machine runs on (each world's fleet key, the LiveKit pair, the store's secret), declared with no value; a worker identity per world that reads its own fleet key and the three shared ones; the box's identity (`pinecall-fleet`) may read and add versions | `pinecall-worker-production@…`, `pinecall-worker-sandbox@…`; values written by `cell publish-secrets` |

`environments/lab` holds the voice lab alone (`infra/lab/README.md`): `pinecall-lab-box`, `-gen`
and, while a run lasts, `-wk`, each the box's first boot with no ssh key (gcloud reaches them),
labelled `pinecall-role=lab`, resized by an apply (`allow_stopping_for_update`, the lab's alone),
and `pinecall-lab`, the firewall rule that opens everything between the three and nothing else.
Its state is empty between runs: `measure.py` ends with `terraform destroy`.

`environments/production/imports.tf` took each of them in on 2026-10-02: 27 resources, made by
hand before, now `No changes.` on a plan. The rules of the old box (v1, tag `pinecall-v2-box`:
`pinecall-web`, `pinecall-sip-signalling`, `pinecall-sip-denied`, `pinecall-media`) are not here:
they go with that machine when it is archived.

## Running it

```console
$ make tf-init ENV=production     # once per checkout: providers, and the state in its bucket
$ make tf-plan ENV=production     # what would change; read it whole
$ make tf-apply ENV=production    # the same plan, applied after `yes`
$ make tf-check                   # every root formatted and valid, no backend reached (CI runs it)
```

Google's credentials are the operator's gcloud login, handed to Terraform as a short-lived token
in the environment (`gcloud auth print-access-token`), never printed or written; AWS's are the
operator's `~/.aws`. Neither is in a file of the repository.

## The state

`pinecall-terraform-state-000000000000` (us-central1): versioned (old states kept 90 days), public
access prevented, uniform access, readable by the project's owners alone. Made by `bootstrap/`
with a local state that may be lost: the bucket outlives it, and is never destroyed by Terraform
(`force_destroy = false`).

**No secret enters the state.** A secret's value is never a Terraform resource: Secret Manager's
secrets are declared here, their versions written by the box (`pinecall-runtime cell publish-secrets`); an AWS access
key is made by hand and sealed on the box (`install.sh secret …`), its user and policy declared
here.

## The box's identity

The box's VM acts as `pinecall-fleet@…` (`environments/production/identities.tf`): a custom role
`pinecallFleetLoop` (read and abandon from a group, read and delete a machine and its disk)
conditioned on the names `pinecall-workers-*` and `pinecall-worker-*`, so no other VM of the
project is the box's to touch; Monitoring's metric writer and Logging's writer; and, from
`modules/secrets`, reading and adding versions of the fleet's secrets. What Terraform puts in the
box's metadata (`pinecall-cloud`, `pinecall-fleet-loop-<world>`) is what `install.sh` reads to run
the fleet loop and publish the secrets.
