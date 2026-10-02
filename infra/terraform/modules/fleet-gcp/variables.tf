variable "world" {
  type        = string
  description = "production or sandbox: the credentials a machine reads and the image it boots."
}

variable "fleet" {
  type        = string
  description = "The fleet's name, as its workers register with LiveKit (pinecall, pinecall-sandbox)."
}

variable "zone" {
  type = string
}

variable "subnetwork" {
  type = string
}

variable "service_account" {
  type        = string
  description = "The world's worker identity (modules/secrets)."
}

variable "machine_type" {
  type    = string
  default = "e2-standard-8"
}

variable "health_port" {
  type        = number
  description = "PINECALL_WORKER_HTTP_PORT of the world's fleet (infra/box/fleets/<world>.env)."
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
