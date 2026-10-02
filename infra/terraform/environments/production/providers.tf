provider "google" {
  project = var.project
  region  = var.region
  zone    = var.zone
}

# The object store and the mail are on AWS: the credentials are the operator's (~/.aws), never here.
provider "aws" {
  region = var.aws_region
}
