# The voice lab: a box, a generator and a worker machine at the sizes under test, made and
# destroyed by infra/lab/measure.py (`apply` with the sizes, `destroy` after). Each is the box's
# first boot without an ssh key: the lab reaches its machines through gcloud.

locals {
  cloud_init = join("\n", [
    for line in split("\n", file("${path.module}/../../../box/cloud-init.yaml")) : line
    if !strcontains(line, "ssh_authorized_keys") && !strcontains(line, "your-public-key")
  ])
  # The generator is SIPp's machine and the fakes': the box's first boot and sip-tester.
  generator_init = replace(local.cloud_init, "  - rclone\n", "  - rclone\n  - sip-tester\n")
  lab            = { pinecall-role = "lab" }
}

module "box" {
  source                    = "../../modules/machine"
  name                      = "pinecall-lab-box"
  zone                      = var.zone
  machine_type              = var.box_type
  tags                      = ["pinecall-lab"]
  labels                    = local.lab
  disk_size_gb              = 30
  subnetwork                = "default"
  metadata                  = { user-data = local.cloud_init }
  allow_stopping_for_update = true
}

module "generator" {
  source                    = "../../modules/machine"
  name                      = "pinecall-lab-gen"
  zone                      = var.zone
  machine_type              = var.generator_type
  tags                      = ["pinecall-lab"]
  labels                    = local.lab
  disk_size_gb              = 30
  subnetwork                = "default"
  metadata                  = { user-data = local.generator_init }
  allow_stopping_for_update = true
}

module "worker" {
  count                     = var.worker_type == null ? 0 : 1
  source                    = "../../modules/machine"
  name                      = "pinecall-lab-wk"
  zone                      = var.zone
  machine_type              = var.worker_type
  tags                      = ["pinecall-lab"]
  labels                    = local.lab
  disk_size_gb              = 30
  subnetwork                = "default"
  metadata                  = { user-data = local.cloud_init }
  allow_stopping_for_update = true
}

# Between the lab's machines, everything: SIP and RTP from the generator, the worker's LiveKit and
# gateways on the box, the fakes and the bucket on the generator. Nothing from outside but ssh,
# which is the project's own rule.
resource "google_compute_firewall" "lab" {
  name        = "pinecall-lab"
  network     = "default"
  source_tags = ["pinecall-lab"]
  target_tags = ["pinecall-lab"]
  allow {
    protocol = "all"
  }
}

# The box's SIP and LiveKit announce its public address (use_external_ip), so the generator's RTP
# and the worker's media reach it from the lab machines' public addresses, which the rule above
# (internal, by tag) does not cover: the media ports from those addresses alone.
resource "google_compute_firewall" "lab_media" {
  name    = "pinecall-lab-media"
  network = "default"
  source_ranges = [
    for address in compact([module.generator.public_address, try(module.worker[0].public_address, "")]) :
    "${address}/32"
  ]
  target_tags = ["pinecall-lab"]
  allow {
    protocol = "udp"
    ports    = ["7882", "10000-10199"]
  }
  allow {
    protocol = "tcp"
    ports    = ["7881"]
  }
}
