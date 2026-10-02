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
environments/lab/         the same modules at the lab's sizes (infra/lab/measure.py)
```

Google's guidance, followed ([root modules](https://docs.cloud.google.com/docs/terraform/best-practices/root-modules)):
one root module per environment, each with `backend.tf` (its state in the bucket below, under its
own prefix), `versions.tf` (providers pinned to a minor), `providers.tf`, `variables.tf`,
`terraform.tfvars` (committed: it holds no secret), `.terraform.lock.hcl` committed; a few dozen
resources a state; the default workspace only.

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
secrets are declared here, their versions written by the box (`infra/box/secrets.sh`); an AWS access
key is made by hand and sealed on the box (`install.sh secret …`), its user and policy declared
here.
