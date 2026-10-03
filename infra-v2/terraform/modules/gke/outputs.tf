output "cluster" {
  value = google_container_cluster.this.name
}

output "location" {
  value = google_container_cluster.this.location
}

output "nodes_service_account" {
  value = google_service_account.nodes.email
}
