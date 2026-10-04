# The identity kubeip acts as (charts/pinecall): it swaps a core node's ephemeral address for the
# static one modules/edge reserves, and can do nothing else. Workload Identity binds it to the
# chart's Kubernetes service account.

variable "project" {
  type = string
}

variable "name" {
  type        = string
  description = "The world-pair: staging, production."
}

variable "kubernetes_service_account" {
  type        = string
  description = "namespace/name of the service account kubeip's DaemonSet runs as."
  default     = "default/pinecall-kubeip"
}

resource "google_project_iam_custom_role" "kubeip" {
  role_id     = "pinecall_${var.name}_kubeip"
  title       = "Pinecall ${var.name}: kubeip"
  description = "Give a node a reserved static address in place of its ephemeral one."
  permissions = [
    "compute.addresses.get",
    "compute.addresses.list",
    "compute.addresses.use",
    "compute.instances.addAccessConfig",
    "compute.instances.deleteAccessConfig",
    "compute.instances.get",
    "compute.networks.useExternalIp",
    "compute.subnetworks.useExternalIp",
    "compute.zoneOperations.get",
  ]
}

resource "google_service_account" "kubeip" {
  account_id   = "pinecall-${var.name}-kubeip"
  display_name = "Pinecall ${var.name}: kubeip"
}

resource "google_project_iam_member" "kubeip" {
  project = var.project
  role    = google_project_iam_custom_role.kubeip.id
  member  = "serviceAccount:${google_service_account.kubeip.email}"
}

resource "google_service_account_iam_member" "kubeip" {
  service_account_id = google_service_account.kubeip.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project}.svc.id.goog[${var.kubernetes_service_account}]"
}

output "service_account" {
  value = google_service_account.kubeip.email
}
