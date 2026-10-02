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

variable "deploy_user" {
  type        = string
  description = "The account ssh reaches the replica as."
}

variable "deploy_ssh_key" {
  type        = string
  description = "The operator's public ssh key: the box's deploy account and the replica's."
}

variable "carrier_signalling" {
  type        = list(string)
  description = "The carrier's signalling edges (Twilio's), the only sources of 5060."
}
