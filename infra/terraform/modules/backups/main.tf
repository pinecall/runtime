# Where a cluster's Postgres keeps its past: a bucket its WAL and its base backups go to, through
# the Barman Cloud plugin, as an identity of its own that touches this bucket alone (Workload
# Identity: the Postgres pods' Kubernetes service account acts as it). The plugin's retention
# policy (charts/postgres) deletes what is older than the window, so the bucket has no lifecycle:
# a rule of its own could delete a WAL file a kept base backup still needs.

variable "project" {
  type = string
}

variable "name" {
  type        = string
  description = "The world-pair: staging, production."
}

variable "region" {
  type = string
}

# The Postgres pods' Kubernetes service account, and a restore drill's: a Cluster recovered from
# the bucket beside the live one, read as the same identity (charts/postgres, README).
variable "postgres_service_accounts" {
  type        = list(string)
  description = "The Kubernetes service accounts that act as the backups' identity: namespace/name."
  default     = ["default/pinecall-postgres", "default/pinecall-postgres-restore"]
}

data "google_project" "this" {
  project_id = var.project
}

# The backups' identity holds objectAdmin, so it can delete what it wrote: a deleted object is kept
# seven days (Cloud Storage's own default, said here so a plan shows it going), and the identity
# cannot change the bucket to shorten that.
resource "google_storage_bucket" "postgres" {
  name                        = "pinecall-${var.name}-postgres-${data.google_project.this.number}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  soft_delete_policy {
    retention_duration_seconds = 604800
  }
}

resource "google_service_account" "postgres" {
  account_id   = "pinecall-${var.name}-postgres"
  display_name = "Pinecall's Postgres backups, ${var.name}"
}

resource "google_storage_bucket_iam_member" "postgres" {
  bucket = google_storage_bucket.postgres.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.postgres.email}"
}

# The plugin lists the bucket before it writes, which objectAdmin alone does not grant on the bucket.
resource "google_storage_bucket_iam_member" "postgres_reader" {
  bucket = google_storage_bucket.postgres.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.postgres.email}"
}

resource "google_service_account_iam_member" "postgres_identity" {
  for_each           = toset(var.postgres_service_accounts)
  service_account_id = google_service_account.postgres.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project}.svc.id.goog[${each.value}]"
}

# The live identity's binding, from before the drill's was beside it: renamed, never unbound.
moved {
  from = google_service_account_iam_member.postgres_identity
  to   = google_service_account_iam_member.postgres_identity["default/pinecall-postgres"]
}

output "bucket" {
  value = google_storage_bucket.postgres.name
}

output "service_account" {
  value = google_service_account.postgres.email
}
