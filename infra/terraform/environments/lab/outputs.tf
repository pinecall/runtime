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

output "worker_internal_address" {
  value = try(module.worker[0].internal_address, null)
}
