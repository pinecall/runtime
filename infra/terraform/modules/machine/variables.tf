variable "name" {
  type = string
}

variable "zone" {
  type = string
}

variable "machine_type" {
  type = string
}

variable "tags" {
  type    = list(string)
  default = []
}

variable "labels" {
  type    = map(string)
  default = {}
}

variable "disk_size_gb" {
  type = number
}

variable "disk_type" {
  type    = string
  default = "pd-balanced"
}

variable "image" {
  type        = string
  description = "What a new machine boots from; a machine made already keeps its disk (ignored after)."
  default     = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
}

variable "subnetwork" {
  type        = string
  description = "The subnet's self link, or its name in the machine's region."
}

variable "public_address" {
  type        = string
  description = "A static address the machine is reached at; null for an ephemeral one."
  default     = null
}

variable "public" {
  type        = bool
  description = "Whether the machine has a public address at all."
  default     = true
}

variable "metadata" {
  type        = map(string)
  description = "user-data (cloud-init, read at the first boot alone) or ssh-keys."
  default     = {}
}

variable "service_account" {
  type        = string
  description = "The identity the machine acts as; null for none."
  default     = null
}

variable "scopes" {
  type    = list(string)
  default = ["cloud-platform"]
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "allow_stopping_for_update" {
  type        = bool
  description = "Whether Terraform may stop the machine to change its type or identity."
  default     = false
}
