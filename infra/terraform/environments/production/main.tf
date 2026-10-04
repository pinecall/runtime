# Production: Pinecall's own cluster, box.pinecall.io and sandbox.pinecall.io. Until the cutover
# the names point at v1's box (its own Terraform, ../infra-v1): `point_names` turns on with it.

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
  backend "gcs" {
    bucket = "pinecall-terraform-state-209548925515"
    prefix = "cluster/production"
  }
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

# Route 53 holds pinecall.io; its credentials are ~/.aws.
provider "aws" {
  region = "us-east-1"
}

module "edge" {
  source      = "../../modules/edge"
  name        = "production"
  names       = ["box.pinecall.io", "sandbox.pinecall.io"]
  point_names = var.point_names
  # Where a carrier sends each world's calls: the core node's static address (PINECALL_SIP_DOMAIN).
  sip_names = ["sip.box.pinecall.io", "sip.sandbox.pinecall.io"]
  region    = var.region
  # The fence's networks (sip_sources.auto.tfvars.json, `pinecall-runtime fence export`).
  sip_sources = var.sip_sources
}

# On at the cutover: box.pinecall.io and sandbox.pinecall.io point at this cluster.
variable "point_names" {
  type    = bool
  default = false
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

module "secrets" {
  source  = "../../modules/secrets"
  project = var.project
  name    = "production"
  # The vault key the box's database is sealed under, the box's ops key (billing and notify knock
  # with it), and the object store's key, made by hand: all the box's own, carried over.
  given      = ["vault-key", "ops-key", "s3-access-key-id", "s3-secret-access-key"]
  depends_on = [module.gke]
}

module "gke" {
  source              = "../../modules/gke"
  project             = var.project
  name                = "production"
  region              = var.region
  zone                = var.zone
  nodes_range         = "10.120.0.0/22"
  pods_range          = "10.121.0.0/16"
  services_range      = "10.122.0.0/20"
  deletion_protection = true
}

output "cluster" {
  value = module.gke.cluster
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

output "core_address" {
  value = module.edge.core_address
}

output "kubeip_service_account" {
  value = module.kubeip.service_account
}

output "certificate_map" {
  value = module.edge.certificate_map
}
