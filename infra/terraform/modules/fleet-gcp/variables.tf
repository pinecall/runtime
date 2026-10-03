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

