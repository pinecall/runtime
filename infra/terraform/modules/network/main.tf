# The box's side of the VPC: the fleet's subnet, the four rules the box's tag is reached by, and
# its static address. The VPC itself is the project's `default`, shared, and only read here.
data "google_compute_network" "vpc" {
  name = var.network
}

resource "google_compute_subnetwork" "fleet" {
  name          = "pinecall-fleet"
  region        = var.region
  network       = data.google_compute_network.vpc.self_link
  ip_cidr_range = var.fleet_range
  # A fleet machine has no public address: Google's APIs (Secret Manager, Monitoring) through this.
  private_ip_google_access = true
}

resource "google_compute_firewall" "web" {
  name          = "pinecall-runtime-web"
  network       = data.google_compute_network.vpc.self_link
  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = [var.box_tag]
  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
  allow {
    protocol = "tcp"
    ports    = ["80"]
  }
}

resource "google_compute_firewall" "sip" {
  name          = "pinecall-runtime-sip"
  network       = data.google_compute_network.vpc.self_link
  direction     = "INGRESS"
  source_ranges = var.carrier_signalling
  target_tags   = [var.box_tag]
  allow {
    protocol = "tcp"
    ports    = ["5060"]
  }
  allow {
    protocol = "udp"
    ports    = ["5060"]
  }
}

resource "google_compute_firewall" "media" {
  name          = "pinecall-runtime-media"
  network       = data.google_compute_network.vpc.self_link
  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = [var.box_tag]
  allow {
    protocol = "udp"
    ports    = ["10000-10199"]
  }
  allow {
    protocol = "tcp"
    ports    = ["7881"]
  }
  allow {
    protocol = "udp"
    ports    = ["7882"]
  }
}

resource "google_compute_firewall" "fleet_to_box" {
  name          = "pinecall-fleet-to-box"
  description   = "The fleet's worker machines reach the box's LiveKit API and the gateways' balancer"
  network       = data.google_compute_network.vpc.self_link
  direction     = "INGRESS"
  source_ranges = [var.fleet_range]
  target_tags   = [var.box_tag]
  allow {
    protocol = "tcp"
    ports    = ["7880"]
  }
  allow {
    protocol = "tcp"
    ports    = ["8088"]
  }
}

resource "google_compute_address" "box" {
  name         = var.address_name
  region       = var.region
  address_type = "EXTERNAL"
  network_tier = "PREMIUM"
}
