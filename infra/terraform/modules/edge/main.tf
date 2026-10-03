# Where a cluster meets the world: the global address its Ingress serves both names on, the names in
# Route 53 (the zone's other records are other repositories'), and the core node's ports. LiveKit's
# media and SIP's RTP are open to anyone; 5060 only from the networks named (the carriers'),
# everyone else's denied explicitly after them.

resource "google_compute_global_address" "ingress" {
  name = "pinecall-${var.name}-ingress"
}

data "google_compute_network" "vpc" {
  name = var.network
}

resource "google_compute_firewall" "media" {
  name          = "pinecall-${var.name}-media"
  network       = data.google_compute_network.vpc.self_link
  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = [var.core_tag]
  allow {
    protocol = "udp"
    ports    = ["7882", var.rtp_ports]
  }
  allow {
    protocol = "tcp"
    ports    = ["7881"]
  }
}

resource "google_compute_firewall" "sip" {
  count         = length(var.sip_sources) > 0 ? 1 : 0
  name          = "pinecall-${var.name}-sip"
  network       = data.google_compute_network.vpc.self_link
  direction     = "INGRESS"
  priority      = 500
  source_ranges = var.sip_sources
  target_tags   = [var.core_tag]
  allow {
    protocol = "udp"
    ports    = ["5060"]
  }
  allow {
    protocol = "tcp"
    ports    = ["5060"]
  }
}

resource "google_compute_firewall" "sip_deny" {
  name          = "pinecall-${var.name}-sip-deny"
  network       = data.google_compute_network.vpc.self_link
  direction     = "INGRESS"
  priority      = 600
  source_ranges = ["0.0.0.0/0"]
  target_tags   = [var.core_tag]
  deny {
    protocol = "udp"
    ports    = ["5060"]
  }
  deny {
    protocol = "tcp"
    ports    = ["5060"]
  }
}

data "aws_route53_zone" "zone" {
  name = var.zone
}

resource "aws_route53_record" "names" {
  for_each = toset(var.names)
  zone_id  = data.aws_route53_zone.zone.zone_id
  name     = each.value
  type     = "A"
  ttl      = 300
  records  = [google_compute_global_address.ingress.address]
}

variable "name" {
  type = string
}

variable "network" {
  type    = string
  default = "default"
}

variable "core_tag" {
  type        = string
  description = "The network tag of the core node pool (modules/gke)."
  default     = "pinecall-gke-core"
}

variable "rtp_ports" {
  type    = string
  default = "10000-10199"
}

variable "sip_sources" {
  type        = list(string)
  description = "The networks 5060 opens to: the carriers' signalling edges (and, in staging, the lab's generator)."
  default     = []
}

variable "zone" {
  type    = string
  default = "pinecall.io"
}

variable "names" {
  type = list(string)
}

output "ingress_address_name" {
  value = google_compute_global_address.ingress.name
}

output "ingress_address" {
  value = google_compute_global_address.ingress.address
}
