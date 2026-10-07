# Production: a world-pair's cluster. Every name, address, range and account of the operator who
# runs it is a variable, its values in the operator's own <env>.tfvars, and the state bucket is
# theirs too (<env>.backend.hcl): both outside this tree, given by `make tf-init`/`tf-plan` (OPS).

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.0"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # bucket and prefix: the operator's <env>.backend.hcl (`make tf-init`).
  backend "gcs" {}
}

provider "google" {
  project = var.project
  region  = var.region
  zone    = var.zone
}

variable "project" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-c"
}

# Route 53 holds the names' zone (dns_zone); its credentials are the operator's AWS login.
provider "aws" {
  region = "us-east-1"
}

module "edge" {
  source         = "../../modules/edge"
  name           = "production"
  zone           = var.dns_zone
  names          = var.names
  point_names    = var.point_names
  sip_names      = var.sip_names
  kept_addresses = var.kept_addresses
  services       = var.services
  point_services = var.point_services
  region         = var.region
  # The fence's networks (sip_sources.auto.tfvars.json, `pinecall-runtime fence export`).
  sip_sources = var.sip_sources
}

variable "dns_zone" {
  type        = string
  description = "The Route 53 zone every name below is a record of."
}

variable "names" {
  type        = list(string)
  description = "The names the cluster is served at, every one of them both worlds; the first is PINECALL_DOMAIN."
}

variable "sip_names" {
  type        = map(string)
  description = "Each world's SIP name, at the world's media address: a carrier's alone (PINECALL_SIP_DOMAIN, PINECALL_SANDBOX_SIP_DOMAIN)."
}

variable "kept_addresses" {
  type        = map(string)
  description = "A world's media address kept from before, by its reserved name (carriers know it)."
  default     = {}
}

variable "services" {
  type        = list(string)
  description = "Names of services of your own served at the same Gateway, each by its own chart; none by default."
  default     = []
}

variable "alert_emails" {
  type        = list(string)
  description = "Who the cluster's alerts are mailed to."
}

# A notifier of the operator's own signs Android's pushes in this Firebase project (modules/notify);
# unset, no identity for it is made.
variable "firebase_project" {
  type    = string
  default = null
}

# The hosting cluster (modules/hosting): where the orgs' hosted apps run. Off, the runtime serves
# everything but `pinecall deploy`.
variable "hosting" {
  type    = bool
  default = false
}

locals {
  # Each world's runner key, minted by the gateway (`keys runner`) and put by the operator.
  runner_keys = var.hosting ? ["runner-key-production", "runner-key-sandbox"] : []
}

# The names point at this cluster's address; off while another box still answers them.
variable "point_names" {
  type    = bool
  default = true
}

# The services' names point at this cluster's address; off while they are served elsewhere.
variable "point_services" {
  type    = bool
  default = true
}

# The cluster's private ranges (modules/gke): its nodes' subnet, its pods' and its services'. The
# charts are told the first two (media.nodeNetwork, gateway.podNetwork).
variable "ranges" {
  type = object({ nodes = string, pods = string, services = string })
}

# The hosting cluster's, apart from the cluster's (modules/hosting); its runners take fixed
# addresses of `services` (charts/hosting, worlds).
variable "hosting_ranges" {
  type    = object({ nodes = string, pods = string, services = string })
  default = null
}

# Secrets the operator puts by hand, each made empty here: the vault key and the ops key the
# runtime needs, the object store's key when recordings leave the pod, and any of the operator's
# own services' that read the same store.
variable "given_secrets" {
  type    = list(string)
  default = ["vault-key", "ops-key"]
}

# Written by `pinecall-runtime fence export` into sip_sources.auto.tfvars.json: the orgs'
# addresses, never committed. Unset, 5060 opens to nobody.
variable "sip_sources" {
  type    = list(string)
  default = []
}

# Helm reaches the cluster as the gcloud login, by a token Terraform reads, never a stored file.
data "google_client_config" "me" {}

provider "helm" {
  kubernetes = {
    host                   = "https://${module.gke.endpoint}"
    token                  = data.google_client_config.me.access_token
    cluster_ca_certificate = base64decode(module.gke.ca_certificate)
  }
}

# After the cluster and its pools: a chart waits for its pods, which need a node to run on.
module "addons" {
  source                  = "../../modules/addons"
  secrets_service_account = module.secrets.sync_service_account
  depends_on              = [module.gke]
}

module "alerts" {
  source = "../../modules/alerts"
  name   = "production"
  domain = var.names[0]
  emails = var.alert_emails
}

module "backups" {
  source  = "../../modules/backups"
  project = var.project
  name    = "production"
  region  = var.region
}

module "kubeip" {
  source  = "../../modules/kubeip"
  project = var.project
  name    = "production"
}

module "notify" {
  count            = var.firebase_project == null ? 0 : 1
  source           = "../../modules/notify"
  project          = var.project
  name             = "production"
  firebase_project = var.firebase_project
}

module "secrets" {
  source     = "../../modules/secrets"
  project    = var.project
  name       = "production"
  given      = concat(var.given_secrets, local.runner_keys)
  depends_on = [module.gke]
}

module "hosting" {
  count          = var.hosting ? 1 : 0
  source         = "../../modules/hosting"
  project        = var.project
  name           = "production"
  region         = var.region
  nodes_range    = var.hosting_ranges.nodes
  pods_range     = var.hosting_ranges.pods
  services_range = var.hosting_ranges.services
  runner_keys    = { for world in ["production", "sandbox"] : world => module.secrets.ids["runner-key-${world}"] }
}

module "gke" {
  source              = "../../modules/gke"
  project             = var.project
  name                = "production"
  region              = var.region
  zone                = var.zone
  nodes_range         = var.ranges.nodes
  pods_range          = var.ranges.pods
  services_range      = var.ranges.services
  deletion_protection = true
}

output "cluster" {
  value = module.gke.cluster
}

output "hosting" {
  value = var.hosting ? module.hosting[0] : null
}

output "location" {
  value = module.gke.location
}

output "secrets_sync_service_account" {
  value = module.secrets.sync_service_account
}

output "ingress_address_name" {
  value = module.edge.ingress_address_name
}

output "ingress_address" {
  value = module.edge.ingress_address
}

output "postgres_backups_bucket" {
  value = module.backups.bucket
}

output "postgres_backups_service_account" {
  value = module.backups.service_account
}

output "media_addresses" {
  value = module.edge.media_addresses
}

output "kubeip_service_account" {
  value = module.kubeip.service_account
}

output "certificate_map" {
  value = module.edge.certificate_map
}

output "notify_service_account" {
  value = one(module.notify[*].service_account)
}
