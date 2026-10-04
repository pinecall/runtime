output "cluster" {
  value = google_container_cluster.this.name
}

output "location" {
  value = google_container_cluster.this.location
}

output "nodes_service_account" {
  value = google_service_account.nodes.email
}

# What Helm reaches the cluster at: known in a plan whenever the cluster is only updated in place,
# so a change to the cluster never makes the addons' releases look new.
output "endpoint" {
  value = google_container_cluster.this.endpoint
}

output "ca_certificate" {
  value = google_container_cluster.this.master_auth[0].cluster_ca_certificate
}
