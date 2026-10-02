output "group" {
  value = google_compute_instance_group_manager.workers.name
}

output "image" {
  value = data.google_compute_image.worker.name
}
