output "cluster" {
  value = google_container_cluster.hosting.name
}

output "location" {
  value = google_container_cluster.hosting.location
}

output "runner_service_account" {
  value = google_service_account.runner.email
}
