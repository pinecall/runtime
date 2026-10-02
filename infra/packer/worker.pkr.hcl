# The fleet's worker image on Google Cloud: a temporary machine made a worker of the world with
# `pinecall-runtime cell image-worker`, from the box's settings and no credential, frozen into the
# family pinecall-worker-<world>. A machine made from it reads its credentials at its first boot
# from Secret Manager (`cell enroll`). `make image WORLD=production` runs it (infra/packer/README.md).

packer {
  required_plugins {
    googlecompute = {
      source  = "github.com/hashicorp/googlecompute"
      version = "~> 1.1"
    }
  }
}

variable "project" {
  type = string
}

variable "zone" {
  type    = string
  default = "us-central1-c"
}

variable "world" {
  type = string
}

variable "box_address" {
  type        = string
  description = "The box's internal address: LiveKit and the gateways' balancer."
}

variable "seats" {
  type        = number
  description = "The calls each machine of the fleet holds: four per vCPU of the machine type it runs as."
}

variable "package" {
  type        = string
  description = "A wheel's path on this machine: the runtime the image carries."
}

variable "settings" {
  type        = string
  description = "The tar `cell worker-settings <world>` wrote: the box's names, the store, the fleet."
}

variable "access_token" {
  type      = string
  default   = env("GOOGLE_OAUTH_ACCESS_TOKEN")
  sensitive = true
}

source "googlecompute" "worker" {
  project_id              = var.project
  zone                    = var.zone
  access_token            = var.access_token
  source_image_family     = "ubuntu-2404-lts-amd64"
  source_image_project_id = ["ubuntu-os-cloud"]
  machine_type            = "e2-standard-2"
  disk_size               = 30
  ssh_username            = "packer"
  # The box's own first boot: the deploy account, uv beside the runtime.
  metadata_files = {
    user-data = "${path.root}/../box/cloud-init.yaml"
  }
  image_name   = "pinecall-worker-${var.world}-${formatdate("YYYYMMDDhhmmss", timestamp())}"
  image_family = "pinecall-worker-${var.world}"
  image_labels = {
    pinecall = "worker"
    world    = var.world
  }
}

build {
  sources = ["source.googlecompute.worker"]

  provisioner "shell" {
    inline = ["cloud-init status --wait >/dev/null"]
  }

  provisioner "file" {
    source      = var.package
    destination = "/tmp/${basename(var.package)}"
  }

  provisioner "file" {
    source      = var.settings
    destination = "/tmp/settings.tar"
  }

  # No credential is ever on this machine: the settings carry none, and cell image-worker seals
  # nothing. What packer's own ssh left is cleared by the builder before the image is taken.
  provisioner "shell" {
    inline = [
      "sudo uvx --from /tmp/${basename(var.package)} pinecall-runtime cell image-worker ${var.box_address} ${var.world} --calls ${var.seats} --package /tmp/${basename(var.package)} < /tmp/settings.tar",
      "sudo rm -f /tmp/settings.tar /tmp/${basename(var.package)}",
      "test -z \"$(sudo ls /etc/credstore.encrypted 2>/dev/null)\"",
    ]
  }
}
