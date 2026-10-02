variable "zone" {
  type        = string
  description = "The hosted zone's name, as Route 53 holds it."
}

variable "names" {
  type        = list(string)
  description = "The names of the zone that point at the box, its own and its services'."
}

variable "address" {
  type = string
}

variable "ttl" {
  type    = number
  default = 60
}
