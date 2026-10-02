variable "project" {
  type        = string
  description = "The Google Cloud project the box runs in."
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-c"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}
