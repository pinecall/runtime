# infra: Pinecall on Kubernetes

The runtime on a Kubernetes cluster, from nothing, on Google Cloud. Terraform makes everything in
the cloud (the cluster and its pools, the registry, the address, the firewall, the names, the
secrets, the operators the chart relies on); one Helm chart runs the runtime on it. Nothing is
made by hand and nothing is built on a laptop.

| path | what |
|---|---|
| `terraform/bootstrap` | the bucket every other root module keeps its state in, made once with local state |
| `terraform/project` | what every cluster of the project shares: the images' registry and the identity Cloud Build builds them as |
| `terraform/environments/<world-pair>` | one cluster each: `production` (both worlds at one name) and `staging`, made for a proof with calls and destroyed after it |
| `terraform/modules/gke` | a cluster: zonal, Workload Identity; a core pool both worlds share, and for each world a media node and a workers pool from 0 that the cluster autoscaler sizes alone ("A pool a world" below) |
| `terraform/modules/registry` · `build` | where images live, and the identity Cloud Build builds them as (`terraform/project`) |
| `terraform/modules/secrets` | the runtime's secrets, drawn once into Secret Manager, and the identity External Secrets reads them as |
| `terraform/modules/addons` | CloudNativePG with its Barman Cloud plugin and cert-manager, External Secrets and KEDA, each its pinned chart |
| `terraform/modules/backups` | the bucket Postgres's WAL and base backups go to, and the identity that writes them, which touches it alone |
| `terraform/modules/edge` | the global address and a certificate for each name, proved by DNS before it points here, and a certificate of its own for services of the operator's own at the same door (`services`); each world's media address and its SIP name; the firewall (media open, 5060 to the carriers alone); the names in Route 53 |
| `terraform/modules/notify` | optional (`firebase_project`): the Google identity a notifier of the operator's own signs Android's pushes as (Firebase Cloud Messaging alone), bound to its chart's service account |
| `terraform/modules/kubeip` | the identity kubeip acts as: it gives each world's media node the static address `modules/edge` reserves, and can do nothing else |
| `terraform/modules/hosting` | the hosting cluster, optional (`hosting = true`): GKE Autopilot in a VPC of its own, where the orgs' hosted apps run, one gVisor pod each |
| `terraform/modules/lab` | the voice lab's generator, beside a cluster made for a proof and destroyed after it (`lab = false`) |
| `terraform/modules/alerts` | the alerts on the gateways' measures, in Cloud Monitoring, and the addresses they are mailed to |
| `images/pinecall/` | the runtime's image: one for every process, each a `pinecall-runtime` verb (`make image`) |
| `images/postgres/` | the cluster's Postgres: CloudNativePG's operand image with pg_textsearch on it |
| `images/suite/` | every suite, run as a Job inside a cluster (`make suite`) |
| `images/cloudbuild.yaml` | how an image is built: by Cloud Build, as the builds' own identity |
| `charts/postgres/` | the cluster's Postgres under CloudNativePG: its WAL to a bucket as it is written and a base backup each night (the Barman Cloud plugin), 35 days kept |
| `manifests/suite.yaml` | the suites' Job, with a Redis made for the run |
| `charts/pinecall/` | the runtime: two gateways and Redis on the shared core pool; each world's LiveKit, SIP and a few workers on its media node, and its scaled workers on its own pool (KEDA, on the gateway's own number); the overflow, the migrations, the fleets' keys, the nightly retention |
| `charts/hosting/` | the hosting cluster's workloads: a runner per world and the namespace their apps run in, fenced (`make hosting`) |
| `charts/edge/` | the front door, released apart and first: the Gateway (Google's HTTPS load balancer, the Gateway API) with Certificate Manager's certificate, its routes, the HTTP redirect and the backends' policies; its load balancer takes minutes to make, so a reinstall of the runtime never makes it again |
| `values/example.yaml` · `hosting-example.yaml` | an operator's values for the charts, every one an example: copied into `OPS` (below) and filled in; CI renders and scans the charts with them |
| `lab/` | calls with real audio and the vendors faked, against staging, measured (`terraform/modules/lab` is its generator) |
| `local/` | the runtime on a laptop, and the Postgres image of the suites (`make local`, `make db`) |
| `models/` | the open stack: three model servers on one GPU and the providers row that points the runtime at them |
| `seed/prices.csv` | the list prices a box's rates start from (`pinecall-runtime providers prices`) |

## Your own values

Nothing in the charts, the modules or the roots names an operator's own: every name, address,
range, account, provider and bucket is a value or a variable, and an operator's values live
outside this tree, in a directory of their own, `OPS` (`PINECALL_OPS`, else `../ops`, beside this
checkout; a private repository is the place for it). The Makefile reads it:

| `OPS/` | what |
|---|---|
| `<env>.mk` | `PROJECT = <the Google Cloud project>`, and `REGION`, `ZONE` or `CONTEXT` where they are not `us-central1`, `<region>-c` and `gke_<project>_<zone>_pinecall-<env>` |
| `values/<env>.yaml` | every chart's values (`infra/values/example.yaml`, filled in): the name, the secret store's provider and prefix, the ingress, the backups' destination and identity, the ranges, the addresses Terraform made, the registry's images |
| `values/hosting-<env>.yaml` | `charts/hosting`'s (`infra/values/hosting-example.yaml`) |
| `terraform/<env>.tfvars` | the root's variables: the project, the DNS zone, the names, the SIP names, the ranges, services of your own, the secrets you put, who alerts mail |
| `terraform/<env>.backend.hcl` | the state bucket and prefix (`bucket = "…"`, `prefix = "cluster/<env>"`), given to `make tf-init` |
| `terraform/bootstrap.tfvars` · `project.tfvars` · `project.backend.hcl` | the same for the two roots made once |

The charts never guess a provider: the secret store is the values' `secrets.provider`, the front
door's class and annotations `ingress`, Postgres's backups `postgres.backups` (destination,
credentials, the pods' identity), the disks' class `storageClass`, the collector
`monitoring.collector`. Unset where one is required, the release fails and says which.

## From nothing to a release

With gcloud signed in on the project, and the zone of the names on Route 53 (`~/.aws`):

```console
$ terraform -chdir=infra/terraform/bootstrap init && terraform -chdir=infra/terraform/bootstrap apply -var-file=$OPS/terraform/bootstrap.tfvars   # once
$ terraform -chdir=infra/terraform/project init -backend-config=$OPS/terraform/project.backend.hcl && terraform -chdir=infra/terraform/project plan -var-file=$OPS/terraform/project.tfvars -out=plan && terraform -chdir=infra/terraform/project apply plan   # once
$ make tf-init ENV=<env>
$ make tf-plan ENV=<env>            # read it; the plan is saved
$ make tf-apply ENV=<env>           # exactly the plan read
$ gcloud container clusters get-credentials pinecall-<env> --zone <zone>
$ gcloud builds submit infra/images/postgres --config infra/images/cloudbuild.yaml \
    --service-account "$(terraform -chdir=infra/terraform/project output -raw build_service_account)" \
    --substitutions _IMAGE="$(terraform -chdir=infra/terraform/project output -raw registry)/postgres:17.11-pgvector0.8.6-pgtextsearch1.4.0"
$ make image                        # the runtime at this commit
$ make deploy ENV=<env>             # the front door, Postgres, the runtime at that image, the live suite
$ make suite ENV=<env>              # every suite inside the cluster
```

`make image` builds only what commits hold: the runtime's tree, the console's and the widget's
checkouts each clean, and `TAG` this checkout's commit; the image installs `uv.lock`'s versions,
each checked by its hash. Every push and pull request is also scanned (`check.yml`, `supply
chain`): the image's locked dependencies against the advisories (pip-audit), every commit for a
secret (gitleaks), and `infra/` with each chart rendered as production releases it for a
misconfiguration (trivy, HIGH and CRITICAL). What trivy finds and is let stand is
`infra/trivy-ignore.yaml`, each by its path and with why; the owed ones carry a date, past which
the check fails again.

`make deploy` runs the migrations before anything new starts (a pre-upgrade hook), mints each
world's fleet key once at install, waits for every workload, and knocks at the production name.
The secrets never leave Secret Manager but into the pods' environment, each pod the ones its
verb reads: the gateway the database, the signal, the vault key, the operator's key, its own
signing key and the LiveKit pair; a worker and the overflow the LiveKit pair, their fleet key and
the recordings store, never the database, the vault, the operator's or the signing key (rule 24 holds the templates to it);
the retention the database, the vault and the store; the migrations the database. The values
file holds no secret.

The gateway connects to Postgres as `pinecall_app`, a role that reads and writes every table's rows
and changes no table: CloudNativePG makes it from `pinecall-postgres-runtime` (charts/postgres, its
password `postgres-app-password` in Secret Manager), and the migrations, which run as the
database's owner, grant it each release (`PINECALL_DB_APP_ROLE`, waiting two minutes for the role
the first time). The owner keeps the migrations, the nightly retention (it makes and drops the
log's days) and the fleets' keys at install. A gateway that reaches the database cannot drop,
alter or truncate a table. The first release with it needs `make tf-apply` before, for the secret.
charts/postgres reads Secret Manager through a store of its own (`pinecall-postgres`), so a cluster
from nothing comes up in the order `make deploy` keeps: the role's secret exists before
charts/pinecall's migrations wait for the role, and CloudNativePG watches it (`cnpg.io/reload`),
so the role is made the moment the secret lands rather than at its next pass.

Every pod of the runtime's image runs as its user (10001), with no capability, no privilege to
gain, the runtime's syscall filter and a root filesystem it cannot write: what it writes goes to
`/tmp` and its home, each an emptyDir, and to the recordings' (`pinecall.podSecurity`,
`pinecall.containerSecurity`). The third-party images' pods hold to the same, each as its own
user (`pinecall.podSecurityAs`): Redis as the image's 999 with its config file on an emptyDir and
each world's disk made its own, LiveKit and SIP as 10001 (every port they take is above 1024, so
no capability), kubeip as its 1001, and the fleets' keys Job as the runtime's user with kubectl's
home on an emptyDir.
Two NetworkPolicies (`templates/policies.yaml`) name who reaches each store: the gateways' Redis
the gateways alone; Postgres the gateways, the pods labelled `pinecall.io/reaches-postgres` (the
migrations, the fleets' keys, the retention, a suite's Job), its own instances and CloudNativePG's
namespace. They are enforced only once the cluster's dataplane enforces policies (Dataplane V2,
`modules/gke`), which this cluster's does not yet: that change makes the cluster again, and is
planned on its own.

## A pool a world

Both worlds share the core pool (`terraform/modules/gke`, `core`): the gateways, Postgres, Redis
and Pinecall's services. Each world has the rest of a call to itself, so a sandbox call never
shares a machine with a production call: a **media pool** of one node (`media-<world>`,
`media_type`; production's an e2-standard-4, the sandbox's an e2-standard-2) with the world's own
LiveKit, its livekit-sip and its core workers, and a **workers pool** from 0 (`workers-<world>`,
`workers_max`: 10 nodes for production, 3 for the sandbox) for its scaled workers. Each pool is
tainted with its name, so nothing of another world lands there. A sandbox burst fills the
sandbox's node and its pool to its own ceiling, and nothing else.

LiveKit gives a room to a node by load and knows no pool, so each world is a LiveKit cluster of its
own (`pinecall-livekit-<world>`), with SIP on it, the two talking through a Redis of their own
on the world's media node (Redis's pub/sub is one per server, whatever its database: a LiveKit
sharing one with the other world's answered that world's SIP and refused its numbers); the gateway reaches both (`LIVEKIT_URL`, `LIVEKIT_SANDBOX_URL`) and each tells its webhook
the world (`?world=`). A browser reaches production's at `wss://<name>` and the sandbox's at
`wss://<name>/sandbox`: `charts/edge` routes LiveKit's paths under `/sandbox` to the sandbox's
LiveKit with the prefix taken off.

## SIP's address

Google's HTTPS load balancer, the name, carries no UDP: a carrier sends a world's calls to the
world's SIP name (`sipDomains` in the values, `PINECALL_SIP_DOMAIN`, `PINECALL_SANDBOX_SIP_DOMAIN`),
which Route 53 points at the world's media address (`terraform/modules/edge`, `media_addresses`).
kubeip (the chart, as the identity `terraform/modules/kubeip` made) gives each world's media node
its address, found by its label (`pinecall-media=<cluster>-<world>`), and gives it again to a node
that replaces it; the world's LiveKit and SIP announce it (`node_ip`, `nat_1_to_1_ip`). A Twilio
trunk made before the SIP name moved is sent on once, by `pinecall-runtime sip repoint`.

The core node lost, drilled on staging (when it also held LiveKit and SIP): its VM
deleted at 01:27:50 UTC (gone at 01:29:46), the node pool made another at 01:29:48, kubeip gave it
the same address at 01:32:35, the gateways answered at 01:34:46 with Postgres ready, and four
calls then started, every turn answered. A core of one node is down for those minutes; a second
core node is the shape that is not (`docs/scaling.md`).

## HTTPS

The name is served by `charts/edge`'s Gateway (the Gateway API, `gke-l7-global-external-managed`)
on the global address `terraform/modules/edge` reserves, with a Certificate Manager certificate
proved by DNS (a CNAME each in Route 53): it is issued before a name points at the address, so a
cutover moves the names onto a certificate that is valid already. Made on staging:
the certificate active before any load balancer served it, the names answering over HTTPS on it,
plain HTTP redirected, a LiveKit room joined over its WebSocket and calls placed, the address the
Ingress had served kept.

## The secrets the operator puts

Terraform draws most of a cluster's secrets. The ones a world names in `given`
(`terraform/modules/secrets`) are made empty, and the operator puts each once, piped, so that no
state and no terminal holds it: `vault-key` is the key the database is sealed under (a database
moved in from elsewhere, as production's was on 2026-10-04, keeps the key it was sealed under), and `s3-access-key-id` with `s3-secret-access-key` are the object
store's key, made by hand. The environment's `given_secrets` lists them.

```console
$ <the value> | gcloud secrets versions add pinecall-<env>-<name> --data-file=-
```

A recording moves to the object store `store` names in the world's values (endpoint, region,
bucket); unset, it stays on the pod's disk and goes with the pod. Proven on staging with the
lab's store (moto on the generator): two calls, each recording in the bucket,
sealed, under its org and call.

## Hosting

The orgs' hosted apps (`pinecall deploy`, `docs/protocol/hosting.md`) run on a cluster of their
own, `terraform/modules/hosting`, made where the environment says `hosting = true`: a GKE
Autopilot cluster in a VPC of its own, so an org's code never shares a network with the box's
database, its gateways or its calls. Off, the runtime serves everything but hosted apps.

| what | where |
|---|---|
| each app | one pod in `pinecall-apps`, under GKE Sandbox's gVisor (`runtimeClassName: gvisor`): installed by its first container from its runner's sources, run by its second, read-only, as a user that is not root, with no service account token |
| the fence | `charts/hosting`'s network policy: an app's pod reaches the internet and its runner's sources, nothing private (no pod, node or network address), and nothing reaches it |
| each world's runner | a pod of `pinecall-runner` (`pinecall-runtime runner start`), knocking the box at its public name with its world's runner key, read off Secret Manager by the one identity that may (`pinecall-<env>-runner`) |

The two runner keys are minted by the box and put once, piped, never printed:

```console
$ kubectl --context <the box's> exec deploy/pinecall-gateway -- pinecall-runtime keys runner production \
    | gcloud secrets versions add pinecall-<env>-runner-key-production --data-file=-
$ make hosting ENV=<env> TAG=<commit>       # the runners, the namespace and its fence
```

## From a box (history)

Before 2026-10-04 the runtime also ran on a single machine, a "box", and production moved from one
into this cluster that day. That install is gone: nothing here makes a box or runs on one. What is
left is the one-time migration that moved its database, kept for an operator who still holds one:
`make restore-from-box ENV=<env> BOX=<ssh alias>` (refused for `ENV=production`) empties the cluster's schema, makes its two
extensions again, and restores the box's schema `public` and its rows into it as the database's
owner, the dump streamed from the box into the Postgres pod and deleted there. The box's runtime is
stopped first, and the runtime's chart is installed after, so its fleets' keys are minted in the
database it will run on (the install replaces a key secret an earlier install left). Rehearsed on
staging with a box's database of some 43 000 log rows: restored in 43 s, every row and migration
there, the chart installed on it, the live suite green, the box's orgs, fleet and carriers served.

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

The alerts are written once, as a Prometheus rule file: `charts/pinecall/alerts.yaml`. Who
collects the metrics and evaluates the rules is the environment's `monitoring.collector` in
values file (`OPS/values/<env>.yaml`), which both charts read and neither guesses (unset, the release fails):

| `monitoring.collector` | collects | evaluates and tells |
|---|---|---|
| `gmp` (Google Cloud) | Google's Managed Prometheus: a `PodMonitoring` for the gateways and one for Postgres | `terraform/modules/alerts`, a Cloud Monitoring policy for each rule (scoped to the cluster, as a project holds several), mailed to the environment's `emails`, and an uptime check of the name |
| `prometheus-operator` (any other cluster: EKS, AKS, your own) | the cluster's Prometheus: a `PodMonitor` for each | the cluster's Prometheus, `alerts.yaml` carried as the chart's `PrometheusRule`; Alertmanager tells whom it is told to, and the name's check from outside is that cloud's own (a Route 53 health check, a blackbox exporter) |
| `none` | nothing | nothing |

`monitoring.namespace` is where the collector runs, admitted by the Postgres policy to the
metrics' port (9187); the gateways' `/metrics` answer the pod network (`PINECALL_METRICS_FROM`).

The rules: a write to the log slow (p99 over 250 ms for 5 minutes), a fleet over 80% of its seats
for 5 minutes, a vendor over its error line for 2 minutes; no gateway answering its collector, a
gateway's reaper or sweep stopped (`pinecall_loop_running`), Postgres not answering
(`cnpg_collector_up`), each for 2 minutes; the WAL failing to reach the bucket for 15 minutes (the
backups stop there). On Google Cloud, the name failing from outside too (`/.well-known/pinecall`
every minute from Google's regions, more than one failing for 2 minutes: the load balancer, its
certificate, every gateway). Each address in `alert_emails` is verified once, by the code Google
mails it.

## Services of your own

A service of the operator's own (a notifier, billing) runs on the same cluster as its own chart,
released beside the runtime's and never part of it: its name (`services` in the environment) an
`HTTPRoute` on this Gateway under `modules/edge`'s services certificate, its credentials the
secret store's (`given_secrets`), the runtime reached at `http://pinecall-gateway:8080` with the
ops key. A notifier that signs Android's pushes does it as `modules/notify`'s identity
(`firebase_project`).

## Staging, made and destroyed

Staging is made for a proof and destroyed after it: the runtime's own releases first, so no object is left waiting on a controller Terraform removes,
then Terraform:

```console
$ for r in pinecall pinecall-edge pinecall-postgres; do helm --kube-context <staging> uninstall $r --wait; done
$ gcloud storage rm -r "gs://pinecall-staging-postgres-<project number>/**"
$ terraform -chdir=infra/terraform/environments/staging plan -destroy -var-file=$OPS/terraform/staging.tfvars -var lab=true -out=plan && terraform -chdir=infra/terraform/environments/staging apply plan
```

## Proven

On staging: every suite, 3 113 tests, green inside the cluster against Postgres
17.11 under CloudNativePG 1.30.1, pgvector 0.8.6 and pg_textsearch 1.4.0 preloaded, the runtime
connecting as the database's owner, no superuser. The chart released: two gateways, LiveKit and
SIP on the core node's network, each world's two core workers registered with LiveKit under their
own names, the overflow, and KEDA reading the gateway's number (0, no scaled worker). With
calls, the same day, by the lab: 24 of 24 and 32 of 32 started, every turn answered, KEDA growing
to two scaled workers and the autoscaler to two nodes; a worker node reset under 16 calls, 16 of
16 started; a release during 16 calls, none cut; Postgres's pod deleted under 8, every call written
whole. The table and what it says of the core node: `docs/scaling.md`, "The burst".
