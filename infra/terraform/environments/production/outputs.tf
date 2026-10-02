output "box_address" {
  value = module.network.box_address
}

output "box_internal_address" {
  value = module.box.internal_address
}

output "replica_internal_address" {
  value = module.replica.internal_address
}

output "fleet_subnet" {
  value = module.network.fleet_subnet
}

output "buckets" {
  value = [module.store.backups_bucket, module.store.recordings_bucket]
}
