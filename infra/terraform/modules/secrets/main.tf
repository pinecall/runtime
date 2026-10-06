# A cluster's own secrets in the shapes the runtime reads, kept in Secret Manager and synced into
# the cluster by External Secrets Operator acting as an identity that reads these alone (Workload
# Identity). Most are drawn here once, and are in Terraform's state, the private, versioned bucket
# of bootstrap/; nothing prints them. Those named in `given` are the operator's to put, once,
# piped (`gcloud secrets versions add <id> --data-file=-`): a value that already exists elsewhere
# (the vault key every sealed row of a database is under, an access key made by hand) and that
# Terraform's state never holds.

# A Fernet key: 32 random bytes, URL-safe base64 with its padding.
resource "random_id" "vault" {
  byte_length = 32
}

resource "random_id" "ops" {
  byte_length = 24
}

# The gateway's own signing key (PINECALL_TOKEN_KEY): no worker and no LiveKit holds it.
resource "random_password" "token" {
  length  = 48
  special = false
}

resource "random_id" "livekit_key" {
  byte_length = 6
}

resource "random_password" "livekit_secret" {
  length  = 43
  special = false
}

resource "random_id" "redis" {
  byte_length = 24
}

# The password of the gateway's database role (charts/postgres's managed role pinecall_app): it
# reads and writes rows and changes no table. Letters and digits alone: it rides a URI.
resource "random_password" "postgres_app" {
  length  = 40
  special = false
}

locals {
  drawn = {
    "vault-key"             = "${random_id.vault.b64_url}="
    "ops-key"               = "pc_ops_${random_id.ops.hex}"
    "token-key"             = random_password.token.result
    "livekit-api-key"       = "API${random_id.livekit_key.hex}"
    "livekit-api-secret"    = random_password.livekit_secret.result
    "redis-password"        = random_id.redis.hex
    "postgres-app-password" = random_password.postgres_app.result
  }
  values = { for name, value in local.drawn : name => value if !contains(var.given, name) }
  names  = toset(concat(keys(local.values), var.given))
}

resource "google_secret_manager_secret" "this" {
  for_each  = local.names
  secret_id = "pinecall-${var.name}-${each.key}"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "this" {
  for_each    = local.values
  secret      = google_secret_manager_secret.this[each.key].id
  secret_data = each.value
}

# External Secrets Operator's identity in the cluster reads these secrets and nothing else.
resource "google_service_account" "sync" {
  account_id   = "pinecall-${var.name}-secrets"
  display_name = "Pinecall ${var.name}: External Secrets Operator"
}

resource "google_secret_manager_secret_iam_member" "sync" {
  for_each  = local.names
  secret_id = google_secret_manager_secret.this[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.sync.email}"
}

resource "google_service_account_iam_member" "sync" {
  service_account_id = google_service_account.sync.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project}.svc.id.goog[external-secrets/external-secrets]"
}

variable "project" {
  type = string
}

variable "name" {
  type        = string
  description = "staging or production: the secrets are pinecall-<name>-*."
}

variable "given" {
  type        = list(string)
  description = "The secrets the operator puts by hand, made here empty: vault-key, s3-secret-access-key."
  default     = []
}

output "sync_service_account" {
  value = google_service_account.sync.email
}

output "secret_prefix" {
  value = "pinecall-${var.name}-"
}

# Each secret's id in Secret Manager, by its name here.
output "ids" {
  value = { for name, secret in google_secret_manager_secret.this : name => secret.secret_id }
}
