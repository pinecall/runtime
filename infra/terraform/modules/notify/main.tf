# The identity notify sends Android's pushes as (supervisor/apps/notify, its own chart): Firebase
# Cloud Messaging in the Firebase project, and nothing else anywhere. Workload Identity binds it to
# the chart's Kubernetes service account, so notify asks the metadata server for a token Google
# rotates itself; the organization allows no key file.

variable "project" {
  type = string
}

variable "name" {
  type        = string
  description = "The world-pair: staging, production."
}

variable "firebase_project" {
  type        = string
  description = "The Firebase project the app is registered in."
}

variable "kubernetes_service_account" {
  type        = string
  description = "namespace/name of the service account notify runs as."
  default     = "default/pinecall-notify"
}

resource "google_service_account" "notify" {
  account_id   = "pinecall-${var.name}-notify"
  display_name = "Pinecall ${var.name}: notify"
}

resource "google_project_iam_member" "messaging" {
  project = var.firebase_project
  role    = "roles/firebasecloudmessaging.admin"
  member  = "serviceAccount:${google_service_account.notify.email}"
}

resource "google_service_account_iam_member" "notify" {
  service_account_id = google_service_account.notify.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project}.svc.id.goog[${var.kubernetes_service_account}]"
}

output "service_account" {
  value = google_service_account.notify.email
}
