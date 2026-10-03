# infra-v2: Pinecall on Kubernetes

The runtime on a Kubernetes cluster, from nothing, built and proven on Google Cloud. `infra/` (v1:
the box, its scripts and units) stays as it is until production moves here; the plan, phase by
phase with what each proves, is the internal `INFRA-V2-PLAN.md`.

| path | what |
|---|---|
| `images/pinecall/` | the runtime's image: one for every process, each a `pinecall-runtime` verb; `make image-v2` |
| `images/postgres/` | the cluster's Postgres: CloudNativePG's operand image with pg_textsearch on it |
| `images/suite/` | the suites, run as a Job inside a cluster |
| `images/cloudbuild.yaml` | how an image is built: by Cloud Build, as the builds' own identity, never on a laptop |
| `terraform/modules/gke` | a world's cluster: zonal, two node pools (core; workers, scaled by the cluster autoscaler alone) |
| `terraform/modules/registry` · `build` | where images live, and who builds them |
| `terraform/environments/staging` | staging: made for a phase's proof, destroyed after it |
| `manifests/postgres.yaml` | the cluster's Postgres under CloudNativePG |
| `manifests/suite.yaml` | every suite as a Job against that Postgres, with a Redis made for the run |

## From nothing to the suites green on a cluster

```console
$ cd infra-v2/terraform/environments/staging
$ GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)" terraform init
$ GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)" terraform plan -out plan && terraform apply plan
$ gcloud container clusters get-credentials pinecall-staging --zone us-central1-c
$ helm repo add cnpg https://cloudnative-pg.github.io/charts
$ helm upgrade --install cnpg cnpg/cloudnative-pg --version 0.29.1 -n cnpg-system --create-namespace --wait
$ gcloud builds submit images/postgres --config images/cloudbuild.yaml \
    --service-account "$(terraform output -raw build_service_account)" \
    --substitutions _IMAGE="$(terraform output -raw registry)/postgres:17.11-pgvector0.8.6-pgtextsearch1.4.0"
$ kubectl apply -f manifests/postgres.yaml
$ # the suite's image from the checkout's tracked files and TREE.md, then:
$ sed "s|SUITE_IMAGE|<registry>/suite:<tag>|" manifests/suite.yaml | kubectl apply -f -
$ kubectl logs -f job/suite
```

Proven on 2026-10-03 (F0): every suite, 3 113 tests, green inside the staging cluster against
Postgres 17.11 under CloudNativePG 1.30.1, pgvector 0.8.6 and pg_textsearch 1.4.0 preloaded, the
runtime connecting as the database's owner, no superuser.
