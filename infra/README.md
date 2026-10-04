# infra: Pinecall on Kubernetes

The runtime on a Kubernetes cluster, from nothing, on Google Cloud. Terraform makes everything in
the cloud (the cluster and its pools, the registry, the address, the firewall, the names, the
secrets, the operators the chart relies on); one Helm chart runs the runtime on it. Nothing is
made by hand and nothing is built on a laptop.

| path | what |
|---|---|
| `terraform/bootstrap` | the bucket every other root module keeps its state in, made once with local state |
| `terraform/environments/<world-pair>` | one cluster: `staging` today, each phase proven there before production |
| `terraform/modules/gke` | a cluster: zonal, two node pools (core; workers, sized by the cluster autoscaler alone), Workload Identity |
| `terraform/modules/registry` · `build` | where images live, and the identity Cloud Build builds them as |
| `terraform/modules/secrets` | the runtime's secrets, drawn once into Secret Manager, and the identity External Secrets reads them as |
| `terraform/modules/addons` | CloudNativePG with its Barman Cloud plugin and cert-manager, External Secrets and KEDA, each its pinned chart |
| `terraform/modules/backups` | the bucket Postgres's WAL and base backups go to, and the identity that writes them, which touches it alone |
| `terraform/modules/edge` | the global address, the firewall (media open, 5060 to the carriers alone) and both names in Route 53 |
| `images/pinecall/` | the runtime's image: one for every process, each a `pinecall-runtime` verb (`make image`) |
| `images/postgres/` | the cluster's Postgres: CloudNativePG's operand image with pg_textsearch on it |
| `images/suite/` | every suite, run as a Job inside a cluster (`make suite`) |
| `images/cloudbuild.yaml` | how an image is built: by Cloud Build, as the builds' own identity |
| `charts/postgres/` | the cluster's Postgres under CloudNativePG: its WAL to a bucket as it is written and a base backup each night (the Barman Cloud plugin), 35 days kept |
| `manifests/suite.yaml` | the suites' Job, with a Redis made for the run |
| `charts/pinecall/` | the runtime: two gateways, each world's workers (a few on the core node, the rest scaled by KEDA on the gateway's own number), the overflow, LiveKit and SIP on their node's network, Redis, the migrations, the fleets' keys, the nightly retention, the Ingress with Google's certificate |
| `values/<world-pair>.yaml` | a release's names, its secrets' project and prefix, its address |
| `lab/` | calls with real audio and the vendors faked, against staging, measured (`terraform/modules/lab` is its generator) |
| `local/` | the runtime on a laptop, and the Postgres image of the suites (`make local`, `make db`) |
| `models/` | the open stack: three model servers on one GPU and the providers row that points a box at them |
| `seed/prices.csv` | the list prices a box's rates start from (`pinecall-runtime providers prices`) |

## From nothing to a release

With gcloud signed in on the project, and the zone of the two names on Route 53 (`~/.aws`):

```console
$ terraform -chdir=infra/terraform/bootstrap init && terraform -chdir=infra/terraform/bootstrap apply   # once
$ make tf-init ENV=staging
$ make tf-plan ENV=staging          # read it; the plan is saved
$ make tf-apply ENV=staging         # exactly the plan read
$ gcloud container clusters get-credentials pinecall-staging --zone us-central1-c
$ gcloud builds submit infra/images/postgres --config infra/images/cloudbuild.yaml \
    --service-account "$(terraform -chdir=infra/terraform/environments/staging output -raw build_service_account)" \
    --substitutions _IMAGE="$(terraform -chdir=infra/terraform/environments/staging output -raw registry)/postgres:17.11-pgvector0.8.6-pgtextsearch1.4.0"
$ make image                        # the runtime at this commit
$ make deploy ENV=staging           # Postgres, the chart at that image, then the live suite
$ make suite ENV=staging            # every suite inside the cluster
```

`make deploy` runs the migrations before anything new starts (a pre-upgrade hook), mints each
world's fleet key once at install, waits for every workload, and knocks at the production name.
The secrets never leave Secret Manager but into the pods' environment; the values file holds no
secret.

## SIP's address

Google's HTTPS load balancer, the worlds' names, carries no UDP: a carrier sends a world's calls to
a SIP name of its own (`sipDomains` in the values, `PINECALL_SIP_DOMAIN`), which Route 53 points at
the core node's static address (`terraform/modules/edge`, `core_address`). kubeip (the chart, as
the identity `terraform/modules/kubeip` made) gives the core node that address, and gives it again
to a node that replaces it; LiveKit and SIP announce it (`node_ip`, `nat_1_to_1_ip`). A Twilio
trunk made before the SIP name moved is sent on once, by `pinecall-runtime sip repoint`.

## The secrets the operator puts

Terraform draws most of a cluster's secrets. The ones a world names in `given`
(`terraform/modules/secrets`) are made empty, and the operator puts each once, piped, so that no
state and no terminal holds it: production's `vault-key` is the key the box's database is already
sealed under, and `s3-secret-access-key` is the object store's, made by hand.

```console
$ <the value> | gcloud secrets versions add pinecall-<env>-<name> --data-file=-
```

A recording moves to the object store `store` names in the world's values (endpoint, region, key
id, bucket); unset, it stays on the pod's disk and goes with the pod. Proven on staging on
2026-10-04 with the lab's store (moto on the generator): two calls, each recording in the bucket,
sealed, under its org and call.

## Backups, and a restore

Postgres's WAL goes to the bucket of `terraform/modules/backups` as it is written, and a base
backup each night at 03:00 UTC (`charts/postgres`: the Barman Cloud plugin, 35 days kept), as an
identity that touches that bucket alone. A restore is a second Cluster recovered from the bucket,
which the same identity reads as `pinecall-postgres-restore`:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: pinecall-postgres-restore
spec:
  instances: 1
  imageName: <charts/postgres's image>
  postgresql:
    shared_preload_libraries: [pg_textsearch]
  storage: { size: 20Gi, storageClass: standard-rwo }
  serviceAccountTemplate:
    metadata:
      annotations:
        iam.gke.io/gcp-service-account: <terraform output postgres_backups_service_account>
  bootstrap:
    recovery: { source: origin }      # recoveryTarget: { targetTime: "…" } for a minute of the window
  externalClusters:
    - name: origin
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters: { barmanObjectName: pinecall-postgres, serverName: pinecall-postgres }
```

Drilled on staging on 2026-10-04: a base backup in 9 s, the restore ready in 106 s with the same
30 514 rows of `call_log`, the same last entry and the same 48 migrations as the live database.

## Proven

On 2026-10-03, on staging: every suite, 3 113 tests, green inside the cluster against Postgres
17.11 under CloudNativePG 1.30.1, pgvector 0.8.6 and pg_textsearch 1.4.0 preloaded, the runtime
connecting as the database's owner, no superuser. The chart released: two gateways, LiveKit and
SIP on the core node's network, each world's two core workers registered with LiveKit under their
own names, the overflow, and KEDA reading the gateway's number (0, no scaled worker). With
calls, the same day, by the lab: 24 of 24 and 32 of 32 started, every turn answered, KEDA growing
to two scaled workers and the autoscaler to two nodes; a worker node reset under 16 calls, 16 of
16 started; a release during 16 calls, none cut; Postgres's pod deleted under 8, every call written
whole. The table and what it says of the core node: `docs/scaling.md`, "The burst".
