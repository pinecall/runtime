variable "backups_bucket" {
  type = string
}

variable "recordings_bucket" {
  type = string
}

variable "backups_kept_days" {
  type        = number
  description = "The backup bucket's lifecycle: a nightly backup and the WAL archive are forgotten after this."
  default     = 35
}

variable "alerts_from" {
  type        = string
  description = "The one address the alerts' user may send as."
}

variable "mail_domain" {
  type = string
}

variable "mail_addresses" {
  type        = list(string)
  description = "Addresses of the domain verified one by one for SES."
  default     = []
}
