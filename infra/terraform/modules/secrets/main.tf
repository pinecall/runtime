# A cluster's own secrets, drawn here once in the shapes the runtime reads, kept in Secret Manager, and synced into the cluster by External Secrets
# Operator acting as an identity that reads these alone (Workload Identity). They are in Terraform's
# state, which is the private, versioned bucket of bootstrap/; nothing prints them.

# A Fernet key: 32 random bytes, URL-safe base64 with its padding.
resource "random_id" "vault" {
  byte_length = 32
}

resource "random_id" "ops" {
  byte_length = 24
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

locals {
  values = {
    "vault-key"          = "${random_id.vault.b64_url}="
    "ops-key"            = "pc_ops_${random_id.ops.hex}"
    "livekit-api-key"    = "API${random_id.livekit_key.hex}"
    "livekit-api-secret" = random_password.livekit_secret.result
    "redis-password"     = random_id.redis.hex
  }
}

resource "google_secret_manager_secret" "this" {
  for_each  = local.values
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
  for_each  = local.values
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

output "sync_service_account" {
  value = google_service_account.sync.email
}

output "secret_prefix" {
  value = "pinecall-${var.name}-"
}
