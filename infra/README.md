# infra: Pinecall on Kubernetes

The runtime on a Kubernetes cluster, from nothing, on Google Cloud. Terraform makes everything in
the cloud (the cluster and its pools, the registry, the address, the firewall, the names, the
secrets, the operators the chart relies on); one Helm chart runs the runtime on it. Nothing is
made by hand and nothing is built on a laptop.

| path | what |
|---|---|
| `terraform/bootstrap` | the bucket every other root module keeps its state in, made once with local state |
| `terraform/project` | what every cluster of the project shares: the images' registry and the identity Cloud Build builds them as |
| `terraform/environments/<world-pair>` | one cluster each: `production` (box.pinecall.io, sandbox.pinecall.io), Pinecall's own since the cutover of 2026-10-04, and `staging`, made for a proof with calls and destroyed after it |
| `terraform/modules/gke` | a cluster: zonal, two node pools (core; workers, sized by the cluster autoscaler alone), Workload Identity |
| `terraform/modules/registry` · `build` | where images live, and the identity Cloud Build builds them as (`terraform/project`) |
| `terraform/modules/secrets` | the runtime's secrets, drawn once into Secret Manager, and the identity External Secrets reads them as |
| `terraform/modules/addons` | CloudNativePG with its Barman Cloud plugin and cert-manager, External Secrets and KEDA, each its pinned chart |
| `terraform/modules/backups` | the bucket Postgres's WAL and base backups go to, and the identity that writes them, which touches it alone |
| `terraform/modules/edge` | the global address and the names' certificate, proved by DNS before they point here, and a certificate of its own for Pinecall's services at the same door (`services`: notify, billing); the core node's static address and the SIP names; the firewall (media open, 5060 to the carriers alone); the names in Route 53 |
| `terraform/modules/notify` | the Google identity notify signs Android's pushes as (Firebase Cloud Messaging alone), bound to its chart's service account |
| `terraform/modules/alerts` | the alerts on the gateways' measures, in Cloud Monitoring, and the addresses they are mailed to |
| `images/pinecall/` | the runtime's image: one for every process, each a `pinecall-runtime` verb (`make image`) |
| `images/postgres/` | the cluster's Postgres: CloudNativePG's operand image with pg_textsearch on it |
| `images/suite/` | every suite, run as a Job inside a cluster (`make suite`) |
| `images/cloudbuild.yaml` | how an image is built: by Cloud Build, as the builds' own identity |
| `charts/postgres/` | the cluster's Postgres under CloudNativePG: its WAL to a bucket as it is written and a base backup each night (the Barman Cloud plugin), 35 days kept |
| `manifests/suite.yaml` | the suites' Job, with a Redis made for the run |
| `charts/pinecall/` | the runtime: two gateways, each world's workers (a few on the core node, the rest scaled by KEDA on the gateway's own number), the overflow, LiveKit and SIP on their node's network, Redis, the migrations, the fleets' keys, the nightly retention |
| `charts/edge/` | the front door, released apart and first: the Gateway (Google's HTTPS load balancer, the Gateway API) with Certificate Manager's certificate, its routes, the HTTP redirect and the backends' policies; its load balancer takes minutes to make, so a reinstall of the runtime never makes it again |
| `values/<world-pair>.yaml` | a release's names, its secrets' project and prefix, its address |
| `lab/` | calls with real audio and the vendors faked, against staging, measured (`terraform/modules/lab` is its generator) |
| `local/` | the runtime on a laptop, and the Postgres image of the suites (`make local`, `make db`) |
| `models/` | the open stack: three model servers on one GPU and the providers row that points a box at them |
| `seed/prices.csv` | the list prices a box's rates start from (`pinecall-runtime providers prices`) |

## From nothing to a release

With gcloud signed in on the project, and the zone of the two names on Route 53 (`~/.aws`):

```console
$ terraform -chdir=infra/terraform/bootstrap init && terraform -chdir=infra/terraform/bootstrap apply   # once
$ terraform -chdir=infra/terraform/project init && terraform -chdir=infra/terraform/project plan -out=plan && terraform -chdir=infra/terraform/project apply plan   # once
$ make tf-init ENV=<env>
$ make tf-plan ENV=<env>            # read it; the plan is saved
$ make tf-apply ENV=<env>           # exactly the plan read
$ gcloud container clusters get-credentials pinecall-<env> --zone us-central1-c
$ gcloud builds submit infra/images/postgres --config infra/images/cloudbuild.yaml \
    --service-account "$(terraform -chdir=infra/terraform/project output -raw build_service_account)" \
    --substitutions _IMAGE="$(terraform -chdir=infra/terraform/project output -raw registry)/postgres:17.11-pgvector0.8.6-pgtextsearch1.4.0"
$ make image                        # the runtime at this commit
$ make deploy ENV=<env>             # the front door, Postgres, the runtime at that image, the live suite
$ make suite ENV=<env>              # every suite inside the cluster
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

The core node lost, drilled on staging on 2026-10-04: its VM deleted at 01:27:50 UTC (gone at
01:29:46), the node pool made another at 01:29:48, kubeip gave it the same address at 01:32:35,
the gateways answered at 01:34:46 with Postgres ready, and four calls then started, every turn
answered. A box of one core node is down for those minutes; a second core node is the shape that
is not (`docs/scaling.md`).

## HTTPS

The two names are served by `charts/edge`'s Gateway (the Gateway API, `gke-l7-global-external-managed`)
on the global address `terraform/modules/edge` reserves, with a Certificate Manager certificate
proved by DNS (a CNAME each in Route 53): it is issued before a name points at the address, so a
cutover moves the names onto a certificate that is valid already. Made on staging on 2026-10-04:
the certificate active before any load balancer served it, both names answering over HTTPS on it,
plain HTTP redirected, a LiveKit room joined over its WebSocket and calls placed, the address the
Ingress had served kept.

## The secrets the operator puts

Terraform draws most of a cluster's secrets. The ones a world names in `given`
(`terraform/modules/secrets`) are made empty, and the operator puts each once, piped, so that no
state and no terminal holds it: production's `vault-key` is the key the box's database is already
sealed under, and `s3-access-key-id` with `s3-secret-access-key` are the object store's key,
made by hand.

```console
$ <the value> | gcloud secrets versions add pinecall-<env>-<name> --data-file=-
```

A recording moves to the object store `store` names in the world's values (endpoint, region,
bucket); unset, it stays on the pod's disk and goes with the pod. Proven on staging on
2026-10-04 with the lab's store (moto on the generator): two calls, each recording in the bucket,
sealed, under its org and call.

## From a box

A box's database moves into a cluster's Postgres once, at its cutover:
`make restore-from-box ENV=<env> BOX=<ssh alias>` empties the cluster's schema, makes its two
extensions again, and restores the box's schema `public` and its rows into it as the database's
owner, the dump streamed from the box into the Postgres pod and deleted there. The box's runtime is
stopped first, and the runtime's chart is installed after, so its fleets' keys are minted in the
database it will run on (the install replaces a key secret an earlier install left). Rehearsed on
staging on 2026-10-04 with production's database: restored in 43 s, the same 43 515 rows of
`call_log` and 48 migrations, the chart installed on it, the live suite green, production's orgs,
fleet and carriers served.

## Backups, and a restore

Postgres's WAL goes to the bucket of `terraform/modules/backups` as it is written, and a base
backup is taken each night at 03:00 UTC (`charts/postgres`: the Barman Cloud plugin, 35 days
kept), as an identity that touches that bucket alone. WAL alone restores nothing, so a database
just installed or just restored is backed up once by hand, a `Backup` of `method: plugin`. A
restore is a second Cluster recovered from the bucket, which the same identity reads as
`pinecall-postgres-restore`:

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

## Alerts

Google's managed Prometheus reads each gateway's `/metrics` every 30 s (`charts/pinecall`'s
`PodMonitoring`; the collector comes from the pod network, which `PINECALL_METRICS_FROM` admits),
and `terraform/modules/alerts` makes three alerts on what it read, mailed to the environment's
`emails`: a write to the log slow (p99 over 250 ms for 5 minutes), a fleet over 80% of its seats
for 5 minutes, and a vendor over its error line for 2 minutes. Production's channel is verified
(the code Google mailed it, 2026-10-04).

## Pinecall's own services

notify (`supervisor/apps/notify`) and billing (`cloud/`) run on production's cluster since
2026-10-04, each its own repository's chart released beside the runtime's and never part of it:
one pod each, SQLite on its own disk, its name (`notify.pinecall.io`, `billing.pinecall.io`) an
`HTTPRoute` on this Gateway under `modules/edge`'s services certificate, its credentials Secret
Manager's (`given`). Both reach the runtime at `http://pinecall-gateway:8080`; notify signs
Android's pushes as `modules/notify`'s identity. Each repository's `make image` and `make deploy`
release it. Their SQLite files came over from the box once, on 2026-10-04: each service's units
stopped there, the file copied whole by SQLite's own backup onto the pod's disk while no pod ran,
then the pod started.

## Staging, made and destroyed

Staging is made for a proof and destroyed after it (destroyed on 2026-10-04, after the cutover):
the runtime's own releases first, so no object is left waiting on a controller Terraform removes,
then Terraform:

```console
$ for r in pinecall pinecall-edge pinecall-postgres; do helm --kube-context <staging> uninstall $r --wait; done
$ gcloud storage rm -r "gs://pinecall-staging-postgres-<project number>/**"
$ terraform -chdir=infra/terraform/environments/staging plan -destroy -var lab=true -out=plan && terraform -chdir=infra/terraform/environments/staging apply plan
```

## Production

`environments/production` runs Pinecall's own production since the cutover of 2026-10-04 at
03:24–03:40 UTC, with 0 calls up: the box's runtime stopped, its database restored here (43 520
rows of `call_log`, 48 migrations, in 21 s) and backed up, the chart installed on it, the five
Twilio trunks sent on to the SIP names (`sip repoint`), the names moved onto the Gateway's active
certificate, notify and billing on the box pointed at the cluster; the live suite green on both
names, the tenants' agents and the apps runner reconnected on their own.

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
