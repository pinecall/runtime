output "network" {
  value = data.google_compute_network.vpc.self_link
}

output "fleet_subnet" {
  value = google_compute_subnetwork.fleet.self_link
}

output "box_address" {
  value = google_compute_address.box.address
}
