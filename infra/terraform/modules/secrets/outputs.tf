output "worker_service_accounts" {
  description = "Each world's worker identity, by world."
  value       = { for world, account in google_service_account.worker : world => account.email }
}

output "names" {
  value = local.names
}
