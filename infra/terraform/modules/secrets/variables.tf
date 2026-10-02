variable "project" {
  type = string
}

variable "worlds" {
  type        = list(string)
  description = "The worlds whose fleet key a machine may read."
  default     = ["production", "sandbox"]
}

variable "publishers" {
  type        = list(string)
  description = "Who adds versions: the box's identity (`cell publish-secrets`), as IAM members."
}
