# Production: the box, its replica, the network around them, the object store, the mail, the
# names. Every resource here existed before this file did; imports.tf brought each in, and a
# plan with no changes is the proof that this file says what runs.

locals {
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
  metadata            = { user-data = local.cloud_init }
  service_account     = var.box_service_account
  scopes              = var.box_scopes
  deletion_protection = true
}

module "replica" {
  source              = "../../modules/machine"
  name                = "pinecall-runtime-replica"
  zone                = var.zone
  machine_type        = "e2-standard-4"
  disk_size_gb        = 50
  disk_type           = "pd-standard"
  subnetwork          = "default"
  metadata            = { ssh-keys = "${var.deploy_user}:${var.deploy_ssh_key}\n" }
  deletion_protection = true
}

module "store" {
  source            = "../../modules/store"
  backups_bucket    = "pinecall-box-backups-905418191085"
  recordings_bucket = "pinecall-box-recordings-905418191085"
  alerts_from       = "alerts@pinecall.io"
  mail_domain       = "pinecall.io"
  mail_addresses    = ["bernardo@pinecall.io", "info@pinecall.io"]
}

module "dns" {
  source  = "../../modules/dns"
  zone    = "pinecall.io"
  names   = ["box.pinecall.io", "sandbox.pinecall.io", "notify.pinecall.io", "billing.pinecall.io"]
  address = module.network.box_address
}
