variable "region" {
  type = string
}

variable "network" {
  type        = string
  description = "The VPC the box lives in. Not managed here: other machines of the project share it."
  default     = "default"
}

variable "box_tag" {
  type        = string
  description = "The network tag every rule of the box targets."
}

variable "fleet_range" {
  type        = string
  description = "The fleet's subnet: outside 10.128.0.0/9 on an auto-mode network."
}

variable "carrier_signalling" {
  type        = list(string)
  description = "The carrier's signalling edges, the only sources of 5060 (the same list infra/box/nftables.conf holds)."
}

variable "address_name" {
  type = string
}
