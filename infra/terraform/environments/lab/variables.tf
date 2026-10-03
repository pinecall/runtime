variable "project" {
  type        = string
  description = "The Google Cloud project the lab's machines are made in."
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-c"
}

variable "box_type" {
  type        = string
  description = "The box's machine type under test; changed between runs, the box is stopped and resized."
  default     = "e2-standard-4"
}

variable "generator_type" {
  type        = string
  description = "The machine that fakes the vendors and places the calls: never the one measured."
  default     = "e2-standard-8"
}

variable "worker_type" {
  type        = string
  description = "The worker machines' type under test; null for no worker machine."
  default     = null
}

variable "workers" {
  type        = number
  description = "How many worker machines of worker_type: two to kill one with calls on it."
  default     = 1
}
