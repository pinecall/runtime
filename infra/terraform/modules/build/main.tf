# Who builds infra's images: an identity of its own for Cloud Build, never the project's
# default compute account. It reads the source gcloud uploads, pushes to the cluster's registry,
# and writes its logs; nothing else. `gcloud builds submit --service-account` names it.
resource "google_service_account" "build" {
  account_id   = "pinecall-build"
  display_name = "Pinecall image builds (Cloud Build)"
}

resource "google_project_iam_member" "build" {
  for_each = toset(["roles/artifactregistry.writer", "roles/logging.logWriter"])
  project  = var.project
  role     = each.value
  member   = "serviceAccount:${google_service_account.build.email}"
}

# gcloud uploads the source to <project>_cloudbuild before the build reads it.
resource "google_storage_bucket_iam_member" "source" {
  bucket = "${var.project}_cloudbuild"
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.build.email}"
}

variable "project" {
  type = string
}

output "service_account" {
  value = google_service_account.build.id
}
