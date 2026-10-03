# Production: the box, its replica, the network around them, the object store, the mail, the
# names. Every resource here existed before this file did; imports.tf brought each in, and a
# plan with no changes is the proof that this file says what runs.

locals {
  # What the box reads off its metadata (infra/box/install.sh): it is on Google Cloud, and the
  # fleet loop of production runs on it against the managed instance group.
  box_cloud = {
    pinecall-cloud = "gcp"
    pinecall-fleet-loop-production = join("\n", [
      "PINECALL_FLEET=pinecall",
      "PINECALL_FLEET_SEATS=32",
      "PINECALL_FLEET_MAX=10",
      "PINECALL_FLEET_PROJECT=${var.project}",
      "PINECALL_FLEET_ZONE=${var.zone}",
      "PINECALL_FLEET_MIG=pinecall-workers-production",
    ])
  }

  # The box's own cloud-init, its one line that is the operator's filled with the deploy key. A
  # machine made already never reads it again (modules/machine ignores it after).
  cloud_init = replace(
    file("${path.module}/../../../box/cloud-init.yaml"),
    "/ssh-ed25519 AAAA\\.\\.\\.your-public-key\\.\\.\\. you@laptop/",
    var.deploy_ssh_key,
  )
}

module "network" {
  source             = "../../modules/network"
  region             = var.region
  box_tag            = "pinecall-runtime"
  fleet_range        = "10.100.0.0/24"
  carrier_signalling = var.carrier_signalling
  address_name       = "pinecall-runtime-ip"
}

module "box" {
  source              = "../../modules/machine"
  name                = "pinecall-runtime"
  zone                = var.zone
  machine_type        = "e2-standard-4"
  tags                = ["pinecall-runtime"]
  disk_size_gb        = 50
  disk_type           = "pd-balanced"
  subnetwork          = "default"
  public_address      = module.network.box_address
  metadata            = merge({ user-data = local.cloud_init }, local.box_cloud)
  service_account     = google_service_account.fleet.email
  scopes              = ["cloud-platform"]
  deletion_protection = true
  # Its identity changed once, on 2026-10-02, with this true for that apply: a stop of 97 s, no
  # call open. False again, so no later apply stops the box unless this line is changed for it.
  allow_stopping_for_update = false
}

module "replica" {
  source              = "../../modules/machine"
  name                = "example-replica"
  zone                = var.zone
  machine_type        = "e2-standard-4"
  disk_size_gb        = 50
  disk_type           = "pd-standard"
  subnetwork          = "default"
  metadata            = { ssh-keys = "${var.deploy_user}:${var.deploy_ssh_key}\n" }
  deletion_protection = true
}

# The orgs' hosted apps (infra/apps): another machine than the box, with no identity at all, so a
# container that escaped gVisor holds nothing of Pinecall's. Made by hand on 2026-09-30, imported.
module "apps" {
  source          = "../../modules/machine"
  name            = "pinecall-apps-1"
  zone            = var.zone
  machine_type    = "e2-medium"
  labels          = { purpose = "hosted-apps" }
  disk_size_gb    = 40
  disk_type       = "pd-standard"
  subnetwork      = "default"
  service_account = null
}

module "store" {
  source            = "../../modules/store"
  backups_bucket    = "pinecall-box-backups-000000000000"
  recordings_bucket = "pinecall-box-recordings-000000000000"
  alerts_from       = "alerts@pinecall.io"
  mail_domain       = "pinecall.io"
  mail_addresses    = ["ops@example.com", "info@pinecall.io"]
}

module "dns" {
  source  = "../../modules/dns"
  zone    = "pinecall.io"
  names   = ["box.pinecall.io", "sandbox.pinecall.io", "notify.pinecall.io", "billing.pinecall.io"]
  address = module.network.box_address
}

module "secrets" {
  source     = "../../modules/secrets"
  project    = var.project
  publishers = ["serviceAccount:${google_service_account.fleet.email}"]
}

module "fleet" {
  source            = "../../modules/fleet-gcp"
  world             = "production"
  fleet             = "pinecall"
  zone              = var.zone
  subnetwork        = module.network.fleet_subnet
  service_account   = module.secrets.worker_service_accounts["production"]
  health_port       = 8082
  min               = 0
  max               = 10
  calls_per_machine = 19
}
