# Where a cluster meets the world: the global address its Gateway serves both names on; the core
# node's own address, static, which SIP and LiveKit's media are reached at under SIP names of their
# own (Google's HTTPS load balancer carries no UDP); the names in Route 53 (the zone's other records
# are other repositories'); and the core node's ports. LiveKit's media and SIP's RTP are open to
# anyone; 5060 only from the networks named (the carriers'), everyone else's denied after them.

resource "google_compute_global_address" "ingress" {
  name = "pinecall-${var.name}-ingress"
}

# The HTTPS certificate of both names, Google-managed and proved by DNS (a CNAME each in Route 53),
# so it is issued before a name points at the load balancer: a cutover moves the names onto a
# certificate already valid. The chart's Gateway serves it through the map.
resource "google_project_service" "certificates" {
  service            = "certificatemanager.googleapis.com"
  disable_on_destroy = false
}

resource "google_certificate_manager_dns_authorization" "names" {
  for_each   = toset(var.names)
  name       = "pinecall-${var.name}-${replace(each.value, ".", "-")}"
  domain     = each.value
  depends_on = [google_project_service.certificates]
}

resource "aws_route53_record" "authorizations" {
  for_each = google_certificate_manager_dns_authorization.names
  zone_id  = data.aws_route53_zone.zone.zone_id
  name     = each.value.dns_resource_record[0].name
  type     = each.value.dns_resource_record[0].type
  ttl      = 300
  records  = [each.value.dns_resource_record[0].data]
}

resource "google_certificate_manager_certificate" "names" {
  name = "pinecall-${var.name}"
  managed {
    domains            = var.names
    dns_authorizations = [for authorization in google_certificate_manager_dns_authorization.names : authorization.id]
  }
}

resource "google_certificate_manager_certificate_map" "names" {
  name = "pinecall-${var.name}"
}

resource "google_certificate_manager_certificate_map_entry" "names" {
  for_each     = toset(var.names)
  name         = "pinecall-${var.name}-${replace(each.value, ".", "-")}"
  map          = google_certificate_manager_certificate_map.names.name
  hostname     = each.value
  certificates = [google_certificate_manager_certificate.names.id]
}

# kubeip (charts/pinecall) gives it to the core node by its label, and gives it again to the node
# that replaces it: a carrier's trunk and a number's SDP name one address for good.
resource "google_compute_address" "core" {
  name   = "pinecall-${var.name}-core"
  region = var.region
  labels = { pinecall-core = var.name }
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
  for_each = var.point_names ? toset(var.names) : toset([])
  zone_id  = data.aws_route53_zone.zone.zone_id
  name     = each.value
  type     = "A"
  ttl      = 300
  records  = [google_compute_global_address.ingress.address]
}

resource "aws_route53_record" "sip_names" {
  for_each = toset(var.sip_names)
  zone_id  = data.aws_route53_zone.zone.zone_id
  name     = each.value
  type     = "A"
  ttl      = 300
  records  = [google_compute_address.core.address]
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

# Off while another stack points the names elsewhere (production's box, until the cutover): the
# certificate is proved and issued all the same, and the names move when this turns on.
variable "point_names" {
  type    = bool
  default = true
}

variable "sip_names" {
  type        = list(string)
  description = "The names a carrier sends each world's calls to: the core node's static address."
}

variable "region" {
  type    = string
  default = "us-central1"
}

output "certificate_map" {
  value = google_certificate_manager_certificate_map.names.name
}

output "ingress_address_name" {
  value = google_compute_global_address.ingress.name
}

output "ingress_address" {
  value = google_compute_global_address.ingress.address
}

output "core_address" {
  value = google_compute_address.core.address
}
