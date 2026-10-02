# The credentials a fleet machine runs on, kept where Google Cloud hands them to a machine's own
# identity: the world's fleet key, the LiveKit pair, the object store's secret. Declared here; their
# versions (the values) are written by the box (`pinecall-runtime cell publish-secrets`) and never
# by Terraform, so no value is ever in the state.

locals {
  names = concat(
    [for world in var.worlds : "pinecall-fleet-key-${world}"],
    ["livekit-api-key", "livekit-api-secret", "pinecall-s3-secret-access-key"],
  )
}

resource "google_project_service" "secret_manager" {
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

resource "google_secret_manager_secret" "credential" {
  for_each  = toset(local.names)
  secret_id = each.value
  labels = {
    pinecall = "box"
  }
  replication {
    auto {}
  }
  depends_on = [google_project_service.secret_manager]
}

# What a fleet machine of a world acts as: it reads that world's fleet key and the three the
# worlds share, nothing else, and writes its metrics and logs.
resource "google_service_account" "worker" {
  for_each     = toset(var.worlds)
  account_id   = "pinecall-worker-${each.value}"
  display_name = "Pinecall ${each.value} worker machines: read their credentials at boot"
}

locals {
  shared = ["livekit-api-key", "livekit-api-secret", "pinecall-s3-secret-access-key"]
  reads = {
    for pair in flatten([
      for world in var.worlds : [
        for name in concat(["pinecall-fleet-key-${world}"], local.shared) : { world = world, name = name }
      ]
    ]) : "${pair.world} ${pair.name}" => pair
  }
}

resource "google_secret_manager_secret_iam_member" "worker_reads" {
  for_each  = local.reads
  secret_id = google_secret_manager_secret.credential[each.value.name].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker[each.value.world].email}"
}

resource "google_project_iam_member" "worker_writes" {
  for_each = {
    for pair in setproduct(var.worlds, ["roles/monitoring.metricWriter", "roles/logging.logWriter"]) :
    "${pair[0]} ${pair[1]}" => { world = pair[0], role = pair[1] }
  }
  project = var.project
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.worker[each.value.world].email}"
}

# The box compares before it adds (`publish-secrets` writes a version only where one differs).
resource "google_secret_manager_secret_iam_member" "box_publishes" {
  for_each = {
    for pair in setproduct(keys(google_secret_manager_secret.credential), var.publishers) :
    "${pair[0]} ${pair[1]}" => { secret = pair[0], member = pair[1] }
  }
  secret_id = google_secret_manager_secret.credential[each.value.secret].id
  role      = "roles/secretmanager.secretVersionManager"
  member    = each.value.member
}

resource "google_secret_manager_secret_iam_member" "box_reads" {
  for_each = {
    for pair in setproduct(keys(google_secret_manager_secret.credential), var.publishers) :
    "${pair[0]} ${pair[1]}" => { secret = pair[0], member = pair[1] }
  }
  secret_id = google_secret_manager_secret.credential[each.value.secret].id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member
}
