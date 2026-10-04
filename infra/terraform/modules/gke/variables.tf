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

# A media node per world: its LiveKit and its SIP carry every call's audio, and its core workers
# a quiet hour's calls.
variable "media_type" {
  type        = map(string)
  description = "Each world's media node: production's and the sandbox's machine type."
  default     = { production = "e2-standard-4", sandbox = "e2-standard-2" }
}

variable "workers_type" {
  type    = string
  default = "e2-standard-8"
}

variable "workers_max" {
  type        = map(number)
  description = "Each world's most scaled-worker nodes (32 seats each): its own ceiling."
  default     = { production = 10, sandbox = 3 }
}

variable "deletion_protection" {
  type    = bool
  default = true
}
