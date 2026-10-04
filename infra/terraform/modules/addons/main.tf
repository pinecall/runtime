# The operators a cluster runs Pinecall on, each the standard one for its job and pinned:
# CloudNativePG (Postgres) with its Barman Cloud plugin (WAL and base backups to modules/backups'
# bucket) and cert-manager, which the plugin's TLS needs; External Secrets Operator (Secret Manager
# into the cluster, as the identity modules/secrets made); KEDA (the scaled workers, on the
# gateway's own number). Pinecall's own charts (infra/charts) are released apart, by `make deploy`.

resource "helm_release" "cnpg" {
  name             = "cnpg"
  namespace        = "cnpg-system"
  create_namespace = true
  repository       = "https://cloudnative-pg.github.io/charts"
  chart            = "cloudnative-pg"
  version          = var.cnpg_chart
  wait             = true
}

resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  namespace        = "cert-manager"
  create_namespace = true
  repository       = "https://charts.jetstack.io"
  chart            = "cert-manager"
  version          = var.cert_manager_chart
  wait             = true
  set = [
    {
      name  = "crds.enabled"
      value = "true"
    },
  ]
}

# In the operator's namespace, as the plugin requires.
resource "helm_release" "barman_cloud" {
  name       = "plugin-barman-cloud"
  namespace  = "cnpg-system"
  repository = "https://cloudnative-pg.github.io/charts"
  chart      = "plugin-barman-cloud"
  version    = var.barman_cloud_chart
  wait       = true
  depends_on = [helm_release.cnpg, helm_release.cert_manager]
}

resource "helm_release" "external_secrets" {
  name             = "external-secrets"
  namespace        = "external-secrets"
  create_namespace = true
  repository       = "https://charts.external-secrets.io"
  chart            = "external-secrets"
  version          = var.external_secrets_chart
  wait             = true
  set = [
    {
      name  = "serviceAccount.annotations.iam\\.gke\\.io/gcp-service-account"
      value = var.secrets_service_account
    },
  ]
}

resource "helm_release" "keda" {
  name             = "keda"
  namespace        = "keda"
  create_namespace = true
  repository       = "https://kedacore.github.io/charts"
  chart            = "keda"
  version          = var.keda_chart
  wait             = true
}

variable "secrets_service_account" {
  type        = string
  description = "The Google identity External Secrets Operator acts as (modules/secrets)."
}

variable "cnpg_chart" {
  type    = string
  default = "0.29.1"
}

variable "external_secrets_chart" {
  type    = string
  default = "2.11.0"
}

variable "cert_manager_chart" {
  type    = string
  default = "v1.21.2"
}

variable "barman_cloud_chart" {
  type    = string
  default = "0.8.1"
}

variable "keda_chart" {
  type    = string
  default = "2.21.0"
}
