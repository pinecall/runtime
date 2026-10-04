# Made by environments/staging before the project had a root of its own (2026-10-03), moved here.
import {
  to = module.registry.google_artifact_registry_repository.images
  id = "projects/hiding-place-447317-c6/locations/us-central1/repositories/pinecall"
}

import {
  to = module.build.google_service_account.build
  id = "projects/hiding-place-447317-c6/serviceAccounts/pinecall-build@hiding-place-447317-c6.iam.gserviceaccount.com"
}

import {
  to = module.build.google_project_iam_member.build["roles/artifactregistry.writer"]
  id = "hiding-place-447317-c6 roles/artifactregistry.writer serviceAccount:pinecall-build@hiding-place-447317-c6.iam.gserviceaccount.com"
}

import {
  to = module.build.google_project_iam_member.build["roles/logging.logWriter"]
  id = "hiding-place-447317-c6 roles/logging.logWriter serviceAccount:pinecall-build@hiding-place-447317-c6.iam.gserviceaccount.com"
}

import {
  to = module.build.google_storage_bucket_iam_member.source
  id = "b/hiding-place-447317-c6_cloudbuild roles/storage.objectViewer serviceAccount:pinecall-build@hiding-place-447317-c6.iam.gserviceaccount.com"
}
