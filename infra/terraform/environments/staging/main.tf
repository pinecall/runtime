# Staging: the whole cluster on Google Cloud, where each phase is proven with calls before
# production. Made for the proof and destroyed after it (a second cluster's fee is paid while it
# stands; the free tier covers one zonal cluster).

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
    bucket = "pinecall-terraform-state-000000000000"
    prefix = "cluster/staging"
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
  source = "../../modules/edge"
  name   = "staging"
  names  = ["staging.pinecall.io", "sandbox.staging.pinecall.io"]
  # Staging takes calls from the lab's generator alone, while it stands.
  sip_sources = [for lab in module.lab : "${lab.public_address}/32"]
}

# The voice lab beside the cluster, for a proof with calls (infra/lab): `-var lab=true`.
variable "lab" {
  type    = bool
  default = false
}

module "lab" {
  count      = var.lab ? 1 : 0
  source     = "../../modules/lab"
  zone       = var.zone
  pods_range = "10.111.0.0/16"
}

# Helm reaches the cluster as the gcloud login, by a token Terraform reads, never a stored file.
data "google_client_config" "me" {}

data "google_container_cluster" "this" {
  name       = module.gke.cluster
  location   = var.zone
  depends_on = [module.gke]
}

provider "helm" {
  kubernetes = {
    host                   = "https://${data.google_container_cluster.this.endpoint}"
    token                  = data.google_client_config.me.access_token
    cluster_ca_certificate = base64decode(data.google_container_cluster.this.master_auth[0].cluster_ca_certificate)
  }
}

module "addons" {
  source                  = "../../modules/addons"
  secrets_service_account = module.secrets.sync_service_account
}

module "registry" {
  source = "../../modules/registry"
  region = var.region
}

module "build" {
  source  = "../../modules/build"
  project = var.project
}

module "secrets" {
  source     = "../../modules/secrets"
  project    = var.project
  name       = "staging"
  depends_on = [module.gke]
}

module "gke" {
  source              = "../../modules/gke"
  project             = var.project
  name                = "staging"
  region              = var.region
  zone                = var.zone
  nodes_range         = "10.110.0.0/22"
  pods_range          = "10.111.0.0/16"
  services_range      = "10.112.0.0/20"
  deletion_protection = false
}

output "cluster" {
  value = module.gke.cluster
}

output "location" {
  value = module.gke.location
}

output "registry" {
  value = module.registry.url
}

output "build_service_account" {
  value = module.build.service_account
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

output "lab_generator" {
  value = [for lab in module.lab : { name = lab.name, internal = lab.internal_address, public = lab.public_address }]
}
