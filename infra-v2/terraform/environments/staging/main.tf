# Staging: the whole of infra-v2 on Google Cloud, where each phase is proven with calls before
# production. Made for the proof and destroyed after it (a second cluster's fee is paid while it
# stands; the free tier covers one zonal cluster).

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  backend "gcs" {
    bucket = "pinecall-terraform-state-209548925515"
    prefix = "infra-v2/staging"
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

module "registry" {
  source = "../../modules/registry"
  region = var.region
}

module "build" {
  source  = "../../modules/build"
  project = var.project
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
