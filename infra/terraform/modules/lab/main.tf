# The voice lab's generator, beside a cluster made for a proof: SIPp's callers, the vendors faked
# on their own wire (infra/lab/fake_vendors.py) and the tenant's agent, on one machine reached
# through gcloud, made for the proof and destroyed after it (`lab = false`).

variable "zone" {
  type = string
}

variable "machine_type" {
  type    = string
  default = "e2-standard-8"
}

variable "pods_range" {
  type        = string
  description = "The cluster's pods: the workers reach the fakes from these addresses."
}

variable "network" {
  type    = string
  default = "default"
}

# Its first boot: SIPp, uv and Node, nothing of Pinecall; infra/lab/generator.sh does the rest.
locals {
  first_boot = <<-EOT
    #cloud-config
    package_update: true
    packages: [sip-tester, curl, xz-utils, python3]
    runcmd:
      - curl -LsSf https://astral.sh/uv/0.9.30/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
      - curl -fsSL https://nodejs.org/dist/v24.9.0/node-v24.9.0-linux-x64.tar.xz | tar xJ -C /opt
      - ln -sf /opt/node-v24.9.0-linux-x64/bin/node /opt/node-v24.9.0-linux-x64/bin/npm /usr/local/bin/
  EOT
}

resource "google_compute_instance" "generator" {
  name         = "pinecall-lab-gen"
  zone         = var.zone
  machine_type = var.machine_type
  tags         = ["pinecall-lab"]
  labels       = { pinecall-role = "lab" }
  metadata     = { user-data = local.first_boot }

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = 30
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = var.network
    access_config {}
  }
}

# The workers' plugins reach the fakes from the pods' addresses, inside the VPC.
resource "google_compute_firewall" "fakes" {
  name          = "pinecall-lab-fakes"
  network       = var.network
  source_ranges = [var.pods_range]
  target_tags   = ["pinecall-lab"]
  allow {
    protocol = "tcp"
    ports    = ["8700"]
  }
}

# SIP's answers and the call's RTP come back from the SIP node's public address, which a node's
# replacement changes: SIPp's ports, from anywhere, on a machine that lives for one proof.
resource "google_compute_firewall" "callers" {
  name          = "pinecall-lab-callers"
  network       = var.network
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["pinecall-lab"]
  allow {
    protocol = "udp"
    ports    = ["5060", "6000-6100"]
  }
}

output "name" {
  value = google_compute_instance.generator.name
}

output "internal_address" {
  value = google_compute_instance.generator.network_interface[0].network_ip
}

output "public_address" {
  value = google_compute_instance.generator.network_interface[0].access_config[0].nat_ip
}
