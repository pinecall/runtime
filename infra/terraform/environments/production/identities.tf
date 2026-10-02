# The identity the fleet loop acts as: it makes and deletes worker machines. The box's VM takes it
# in C3 of the plan (internal-docs/runtime-v2/INFRA-AS-CODE-PLAN.md); today it runs as the
# project's default compute account (var.box_service_account).
resource "google_service_account" "fleet" {
  account_id   = "pinecall-fleet"
  display_name = "Pinecall fleet loop: creates and deletes worker machines"
}
