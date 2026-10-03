# A world's fleet of worker machines on Google Cloud: a managed instance group from the image
# family Packer builds, healed on the worker's health port, with no autoscaler. The fleet loop on
# the box is the one thing that sizes it (infra/fleet/gcp-mig.py): it makes a machine when the
# fleet is busy over its target, and lets one go only once it is cordoned and its calls ended; a
# group that removed a machine itself would give it 90 s, and a call may take ten minutes.

data "google_compute_image" "worker" {
  family = "pinecall-worker-${var.world}"
}

resource "google_compute_instance_template" "worker" {
  name_prefix  = "pinecall-worker-${var.world}-"
  machine_type = var.machine_type
  tags         = ["pinecall-worker"]
  labels = {
    pinecall = "worker"
    world    = var.world
  }

  disk {
    source_image = data.google_compute_image.worker.self_link
    boot         = true
    auto_delete  = true
    disk_size_gb = 30
    disk_type    = "pd-balanced"
  }

  # No access_config: no public address; out through the fleet's NAT (modules/network).
  network_interface {
    subnetwork = var.subnetwork
  }

  service_account {
    email  = var.service_account
    scopes = ["cloud-platform"]
  }

  # What `cell enroll` reads at the first boot: from where, and which world's credentials.
  metadata = {
    pinecall-cloud = "gcp"
    pinecall-world = var.world
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
    create_before_destroy = true
  }
}

resource "google_compute_health_check" "worker" {
  name                = "pinecall-worker-${var.world}"
  check_interval_sec  = 10
  timeout_sec         = 5
  healthy_threshold   = 1
  unhealthy_threshold = 3
  http_health_check {
    port         = var.health_port
    request_path = "/"
  }
}

resource "google_compute_instance_group_manager" "workers" {
  name               = "pinecall-workers-${var.world}"
  zone               = var.zone
  base_instance_name = "pinecall-worker-${var.world}"

  version {
    instance_template = google_compute_instance_template.worker.self_link_unique
  }

  # Boot, enrolment and the worker's registration take under two minutes; a machine still
  # unhealthy after five is remade.
  auto_healing_policies {
    health_check      = google_compute_health_check.worker.id
    initial_delay_sec = 300
  }

  # A new template is taken by the machines made from then on; a running one, holding calls, is
  # never replaced by the group (the loop lets it go when it is one too many).
  update_policy {
    type                  = "OPPORTUNISTIC"
    minimal_action        = "REPLACE"
    max_surge_fixed       = 1
    max_unavailable_fixed = 0
  }

  # The loop owns the size: each machine it makes or deletes moves it by one.
  lifecycle {
    ignore_changes = [target_size]
  }
}
