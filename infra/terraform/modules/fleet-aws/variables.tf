variable "world" {
  type        = string
  description = "production or sandbox: the credentials a machine reads and the AMI it boots."
}

variable "fleet" {
  type        = string
  description = "The fleet's name, as its workers register with LiveKit (pinecall, pinecall-sandbox)."
}

variable "subnets" {
  type        = list(string)
  description = "Private subnets with a way out (a NAT gateway) and a route to the box."
}

variable "security_groups" {
  type        = list(string)
  description = "Nothing in; out to the box's LiveKit and gateways, the vendors and Secrets Manager."
}

variable "instance_profile" {
  type        = string
  description = "The world's worker instance profile (modules/secrets-aws)."
}

variable "instance_type" {
  type    = string
  default = "c7a.2xlarge"
}

variable "min" {
  type    = number
  default = 0
}

variable "max" {
  type    = number
  default = 10
}

variable "calls_per_machine" {
  type        = number
  description = "The calls a machine is kept at: its seats × the loop's 0.6, so the group grows before the worker's 0.7."
}
