# A runtime on AWS, its fleet's part: the root module a box on AWS would add to its environment,
# validated by `make tf-check` and applied by nobody (Pinecall's box is on Google Cloud). A box on
# AWS writes these values in its own environments/<name>/ beside its network and its machine.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type = string
}

variable "subnets" {
  type = list(string)
}

variable "security_groups" {
  type = list(string)
}

variable "box_role" {
  type        = string
  description = "The IAM role the box's machine acts as."
}

module "secrets" {
  source          = "../../modules/secrets-aws"
  publisher_roles = [var.box_role]
}

module "fleet" {
  source           = "../../modules/fleet-aws"
  world            = "production"
  fleet            = "pinecall"
  subnets          = var.subnets
  security_groups  = var.security_groups
  instance_profile = module.secrets.worker_instance_profiles["production"]
}
