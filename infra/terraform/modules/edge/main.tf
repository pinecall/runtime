# Where a cluster meets the world: the global address its Gateway serves both names on; the core
# node's own address, static, which SIP and LiveKit's media are reached at under SIP names of their
# own (Google's HTTPS load balancer carries no UDP); the names in Route 53 (the zone's other records
# are other repositories'); and the core node's ports. LiveKit's media and SIP's RTP are open to
# anyone; 5060 only from the networks named (the carriers'), everyone else's denied after them.

resource "google_compute_global_address" "ingress" {
  name = "pinecall-${var.name}-ingress"
}

# Each name's HTTPS certificate, Google-managed and proved by DNS (a CNAME each in Route 53), so it
# is issued before the name points at the load balancer: a cutover moves a name onto a certificate
# already valid. One certificate a name, so adding or retiring a name never reissues another's.
# The chart's Gateway serves them through the map.
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

resource "google_certificate_manager_certificate" "each" {
  for_each = toset(var.names)
  name     = "pinecall-${var.name}-${replace(each.value, ".", "-")}"
  managed {
    domains            = [each.value]
    dns_authorizations = [google_certificate_manager_dns_authorization.names[each.value].id]
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
  certificates = [google_certificate_manager_certificate.each[each.value].id]
}

# Pinecall's own services at the same front door (notify, billing; their charts are their own
# repositories'): a certificate of their own in the same map, so adding one never reissues the
# worlds' certificate, which a name in use is served on.
resource "google_certificate_manager_dns_authorization" "services" {
  for_each   = toset(var.services)
  name       = "pinecall-${var.name}-${replace(each.value, ".", "-")}"
  domain     = each.value
  depends_on = [google_project_service.certificates]
}

resource "aws_route53_record" "service_authorizations" {
  for_each = google_certificate_manager_dns_authorization.services
  zone_id  = data.aws_route53_zone.zone.zone_id
  name     = each.value.dns_resource_record[0].name
  type     = each.value.dns_resource_record[0].type
  ttl      = 300
  records  = [each.value.dns_resource_record[0].data]
}

resource "google_certificate_manager_certificate" "services" {
  count = length(var.services) > 0 ? 1 : 0
  name  = "pinecall-${var.name}-services"
  managed {
    domains            = var.services
    dns_authorizations = [for authorization in google_certificate_manager_dns_authorization.services : authorization.id]
  }
}

resource "google_certificate_manager_certificate_map_entry" "services" {
  for_each     = toset(var.services)
  name         = "pinecall-${var.name}-${replace(each.value, ".", "-")}"
  map          = google_certificate_manager_certificate_map.names.name
  hostname     = each.value
  certificates = [google_certificate_manager_certificate.services[0].id]
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

resource "aws_route53_record" "services" {
  for_each = var.point_services ? toset(var.services) : toset([])
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
  type        = string
  description = "The Route 53 zone the names are records of."
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

variable "services" {
  type        = list(string)
  description = "The names of Pinecall's own services served at this front door, each by its own chart."
  default     = []
}

# Off while the services still answer elsewhere (the box): their certificate is issued all the
# same, and the names move when this turns on.
variable "point_services" {
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
