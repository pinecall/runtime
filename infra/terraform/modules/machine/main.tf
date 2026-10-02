# One VM, as the box, the replica and a lab machine are made: Ubuntu 24.04, cloud-init as its
# first boot, a shielded VM with its vTPM (credentials are sealed to it), live-migrated on host
# maintenance. A machine made already is never replaced for its image or its first boot's data.
resource "google_compute_instance" "this" {
  name                      = var.name
  zone                      = var.zone
  machine_type              = var.machine_type
  tags                      = var.tags
  labels                    = var.labels
  metadata                  = var.metadata
  deletion_protection       = var.deletion_protection
  allow_stopping_for_update = var.allow_stopping_for_update

  boot_disk {
    initialize_params {
      image = var.image
      size  = var.disk_size_gb
      type  = var.disk_type
    }
  }

  network_interface {
    subnetwork = var.subnetwork
    dynamic "access_config" {
      for_each = var.public ? [1] : []
      content {
        nat_ip       = var.public_address
        network_tier = "PREMIUM"
      }
    }
  }

  dynamic "service_account" {
    for_each = var.service_account == null ? [] : [var.service_account]
    content {
      email  = service_account.value
      scopes = var.scopes
    }
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
  }

  shielded_instance_config {
    enable_secure_boot          = false
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  lifecycle {
    ignore_changes = [boot_disk[0].initialize_params[0].image, metadata["user-data"]]
  }
}
