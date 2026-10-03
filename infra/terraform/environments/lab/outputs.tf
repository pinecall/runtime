output "project" {
  value = var.project
}

output "zone" {
  value = var.zone
}

output "box_type" {
  value = var.box_type
}

output "box_internal_address" {
  value = module.box.internal_address
}

output "generator_internal_address" {
  value = module.generator.internal_address
}

output "worker_internal_addresses" {
  value = module.worker[*].internal_address
}

# SIP goes between the lab's machines by their public addresses, as a carrier's would: a number
# hooked from a private network is refused (a carrier reaches the box from the internet).
output "box_public_address" {
  value = module.box.public_address
}

output "generator_public_address" {
  value = module.generator.public_address
}
