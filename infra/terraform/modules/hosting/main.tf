# The hosting cluster: where the orgs' hosted apps run (`pinecall deploy`), apart from the box's
# cluster and in a network of its own, so an org's code never shares a network with the box's
# database, its gateways or its calls. GKE Autopilot: it enforces NetworkPolicy (the hosting chart
# lets an app reach the internet and its runner, nothing private), runs each app's pod under
# GKE Sandbox's gVisor, and is billed by the pod. Each world's runner runs here as a pod whose
# identity reads that world's runner key from Secret Manager and nothing else. Optional: a runtime
# without it serves everything but hosted apps.

resource "google_compute_network" "hosting" {
  name                    = "pinecall-${var.name}-hosting"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "hosting" {
  name          = "pinecall-${var.name}-hosting"
  region        = var.region
  network       = google_compute_network.hosting.id
  ip_cidr_range = var.nodes_range
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_range
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_range
  }
  private_ip_google_access = true
}

# The nodes' own identity: logs, metrics and the runtime's image, nothing of the box's.
resource "google_service_account" "nodes" {
  account_id   = "pinecall-${var.name}-hosting"
  display_name = "Pinecall ${var.name}: the hosting cluster's nodes"
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/artifactregistry.reader",
  ])
  project = var.project
  role    = each.value
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "hosting" {
  name                = "pinecall-${var.name}-hosting"
  location            = var.region
  enable_autopilot    = true
  network             = google_compute_network.hosting.id
  subnetwork          = google_compute_subnetwork.hosting.id
  deletion_protection = var.deletion_protection
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }
  release_channel {
    channel = "REGULAR"
  }
  # Secret Manager's CSI driver: a runner's key mounted as a file, never in the chart.
  secret_manager_config {
    enabled = true
  }
  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.nodes.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }
  depends_on = [google_project_iam_member.nodes]
}

# Each world's runner, as the Kubernetes service account the hosting chart gives its pod.
resource "google_service_account" "runner" {
  account_id   = "pinecall-${var.name}-runner"
  display_name = "Pinecall ${var.name}: the hosted apps' runners"
}

resource "google_service_account_iam_member" "runner" {
  service_account_id = google_service_account.runner.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project}.svc.id.goog[${var.runner_namespace}/runner]"
}

resource "google_secret_manager_secret_iam_member" "runner" {
  for_each  = var.runner_keys
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runner.email}"
}
