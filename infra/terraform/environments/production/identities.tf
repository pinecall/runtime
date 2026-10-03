# The identity the box acts as (its VM's service account): the fleet loop runs on the box, abandons
# and deletes the fleet's machines and tells Cloud Monitoring the fleet's calls; `cell
# publish-secrets` writes the fleet's credentials (modules/secrets grants those).
resource "google_service_account" "fleet" {
  account_id   = "pinecall-fleet"
  display_name = "Pinecall fleet loop: creates and deletes worker machines"
}

# What the loop does to a fleet machine, and nothing more: read and abandon from its group, read
# and delete the machine and its disk.
resource "google_project_iam_custom_role" "fleet_loop" {
  role_id     = "pinecallFleetLoop"
  title       = "Pinecall fleet loop"
  description = "Let go of a machine of a Pinecall worker fleet once it drained."
  permissions = [
    "compute.instanceGroupManagers.get",
    "compute.instanceGroupManagers.update",
    "compute.instances.get",
    "compute.instances.delete",
    "compute.disks.delete",
    "compute.zoneOperations.get",
  ]
}

locals {
  in_the_zone = "projects/${var.project}/zones/${var.zone}"
}

# On the fleet's groups and machines alone: no other VM of the project is the box's to delete.
resource "google_project_iam_member" "fleet_loop" {
  project = var.project
  role    = google_project_iam_custom_role.fleet_loop.id
  member  = "serviceAccount:${google_service_account.fleet.email}"
  condition {
    title       = "the fleets' machines alone"
    description = "pinecall-workers-* groups, pinecall-worker-* machines and their disks"
    expression = join(" || ", [
      "resource.name.startsWith(\"${local.in_the_zone}/instanceGroupManagers/pinecall-workers-\")",
      "resource.name.startsWith(\"${local.in_the_zone}/instances/pinecall-worker-\")",
      "resource.name.startsWith(\"${local.in_the_zone}/disks/pinecall-worker-\")",
    ])
  }
}

resource "google_project_iam_member" "fleet_writes" {
  for_each = toset(["roles/logging.logWriter"])
  project  = var.project
  role     = each.value
  member   = "serviceAccount:${google_service_account.fleet.email}"
}
