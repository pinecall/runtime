# The bucket every other root module keeps its state in, made once with local state: the bucket
# outlives the state that made it (terraform.tfstate here is gitignored and may be lost).
terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project
  region  = var.region
}

variable "project" {
  type        = string
  description = "The Google Cloud project the clusters run in."
}

variable "region" {
  type    = string
  default = "us-central1"
}

data "google_project" "this" {}

resource "google_storage_bucket" "state" {
  name                        = "pinecall-terraform-state-${data.google_project.this.number}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  # Old versions of the state kept 90 days: enough to roll back a bad apply.
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 90
    }
    action {
      type = "Delete"
    }
  }
}

output "state_bucket" {
  value = google_storage_bucket.state.name
}
