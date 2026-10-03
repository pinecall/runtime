variable "project" {
  type = string
}

variable "name" {
  type        = string
  description = "staging or production: the cluster is pinecall-<name>."
}

variable "region" {
  type = string
}

variable "zone" {
  type = string
}

variable "network" {
  type        = string
  description = "The VPC the cluster's subnet is made in (the project's default, read only)."
  default     = "default"
}

variable "nodes_range" {
  type = string
}

variable "pods_range" {
  type = string
}

variable "services_range" {
  type = string
}

variable "core_type" {
  type    = string
  default = "e2-standard-4"
}

variable "core_nodes" {
  type    = number
  default = 1
}

variable "workers_type" {
  type    = string
  default = "e2-standard-8"
}

variable "workers_max" {
  type    = number
  default = 10
}

variable "deletion_protection" {
  type    = bool
  default = true
}
