# A world's Kubernetes cluster on Google Cloud: zonal (the free tier covers one zonal cluster's
# fee), public nodes (LiveKit and SIP need the node's own address: LiveKit supports no private
# cluster), VPC-native on a subnet of its own, Workload Identity for every pod's Google access.
# A pool for what both worlds share (core: the gateways, Postgres, Redis, the box's services), and
# per world a media pool (its LiveKit, its SIP, its core workers, on an address of its own) and a
# workers pool the cluster autoscaler alone grows and shrinks, honouring a worker's grace period
# to drain its calls: a sandbox call never shares a machine with a production call.

resource "google_project_service" "container" {
  project            = var.project
  service            = "container.googleapis.com"
  disable_on_destroy = false
}

data "google_compute_network" "vpc" {
  name = var.network
}

resource "google_compute_subnetwork" "nodes" {
  name          = "pinecall-gke-${var.name}"
  region        = var.region
  network       = data.google_compute_network.vpc.id
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

# The nodes' way out when one has no address of its own: a node kubeip took a static address from
# is left with none until it is given one (production's core node, 2026-10-04: every vendor's call
# from the gateways timed out for 23 minutes). A node with its own address keeps going out by it;
# the NAT serves this cluster's subnet alone, never the VPC's other machines.
resource "google_compute_router" "nodes" {
  name    = "pinecall-gke-${var.name}"
  region  = var.region
  network = data.google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nodes" {
  name                               = "pinecall-gke-${var.name}"
  router                             = google_compute_router.nodes.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = google_compute_subnetwork.nodes.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# What a node may do as itself: write its logs and metrics, pull the cluster's images. Pods act
# as their own identities (Workload Identity), never as the node.
resource "google_service_account" "nodes" {
  account_id   = "pinecall-gke-${var.name}"
  display_name = "Pinecall GKE nodes, ${var.name}"
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

resource "google_container_cluster" "this" {
  name                = "pinecall-${var.name}"
  location            = var.zone
  network             = data.google_compute_network.vpc.id
  subnetwork          = google_compute_subnetwork.nodes.id
  deletion_protection = var.deletion_protection

  # The node pools are this module's own; the default one goes at once.
  remove_default_node_pool = true
  initial_node_count       = 1

  release_channel {
    channel = "REGULAR"
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  workload_identity_config {
    workload_pool = "${var.project}.svc.id.goog"
  }

  # The Gateway API: the HTTPS load balancer charts/pinecall declares, with Certificate Manager's
  # certificate (issued by DNS before a name points here, modules/edge).
  gateway_api_config {
    channel = "CHANNEL_STANDARD"
  }

  # Upgrades of the control plane and the nodes only in the window, when calls are fewest.
  maintenance_policy {
    recurring_window {
      start_time = "2026-01-01T07:00:00Z"
      end_time   = "2026-01-01T11:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SU"
    }
  }

  depends_on = [google_project_service.container]
}

resource "google_container_node_pool" "core" {
  name       = "core"
  cluster    = google_container_cluster.this.id
  location   = var.zone
  node_count = var.core_nodes

  node_config {
    machine_type    = var.core_type
    disk_type       = "pd-balanced"
    disk_size_gb    = 50
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    labels          = { "pinecall.io/pool" = "core" }
    tags            = ["pinecall-gke-${var.name}", "pinecall-gke-core"]
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

# A world's media: its LiveKit and its SIP on the node's own network, and its core workers. The
# taint keeps every other world's pod off; kubeip gives each node its world's static address
# (modules/edge), which LiveKit and SIP announce.
resource "google_container_node_pool" "media" {
  for_each = var.media_type
  name     = "media-${each.key}"
  cluster  = google_container_cluster.this.id
  location = var.zone
  # One: LiveKit and SIP hold the node's ports and announce the world's one address.
  node_count = 1

  node_config {
    machine_type    = each.value
    disk_type       = "pd-balanced"
    disk_size_gb    = 50
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    labels          = { "pinecall.io/pool" = "media-${each.key}" }
    tags            = ["pinecall-gke-${var.name}", "pinecall-gke-media"]
    taint {
      key    = "pinecall.io/pool"
      value  = "media-${each.key}"
      effect = "NO_SCHEDULE"
    }
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

# A world's calls past its core workers (the taint keeps everything else off); the cluster
# autoscaler adds a node when a worker pod has nowhere to go and removes one once its pods are
# gone, waiting out their grace period, so a call is never cut to shrink the pool. Each world's
# ceiling is its own: a sandbox burst never takes production's.
resource "google_container_node_pool" "workers" {
  for_each = var.workers_max
  name     = "workers-${each.key}"
  cluster  = google_container_cluster.this.id
  location = var.zone

  autoscaling {
    min_node_count = 0
    max_node_count = each.value
  }

  node_config {
    machine_type    = var.workers_type
    disk_type       = "pd-balanced"
    disk_size_gb    = 50
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    labels          = { "pinecall.io/pool" = "workers-${each.key}" }
    tags            = ["pinecall-gke-${var.name}", "pinecall-gke-workers"]
    taint {
      key    = "pinecall.io/pool"
      value  = "workers-${each.key}"
      effect = "NO_SCHEDULE"
    }
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
