variable "project" {
  type = string
}

variable "name" {
  type = string
}

variable "region" {
  type = string
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

# The namespace the hosting chart runs the runners in: their identity is bound to it.
variable "runner_namespace" {
  type    = string
  default = "pinecall-runner"
}

# Per world, the Secret Manager id of its runner's key (modules/secrets keeps them).
variable "runner_keys" {
  type = map(string)
}

variable "deletion_protection" {
  type    = bool
  default = true
}
