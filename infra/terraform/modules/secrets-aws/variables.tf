variable "worlds" {
  type    = list(string)
  default = ["production", "sandbox"]
}

variable "publisher_roles" {
  type        = list(string)
  description = "The IAM roles (by name) the box's machine acts as: they read and write every secret here."
  default     = []
}
