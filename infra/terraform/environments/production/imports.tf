# Every resource of production existed before Terraform: each is imported by its id, once. An
# import block left after its import changes nothing; they stay as the record of what was taken in.

import {
  to = module.network.google_compute_subnetwork.fleet
  id = "projects/example-project/regions/us-central1/subnetworks/pinecall-fleet"
}
import {
  to = module.network.google_compute_firewall.web
  id = "projects/example-project/global/firewalls/pinecall-runtime-web"
}
import {
  to = module.network.google_compute_firewall.sip
  id = "projects/example-project/global/firewalls/pinecall-runtime-sip"
}
import {
  to = module.network.google_compute_firewall.media
  id = "projects/example-project/global/firewalls/pinecall-runtime-media"
}
import {
  to = module.network.google_compute_firewall.fleet_to_box
  id = "projects/example-project/global/firewalls/pinecall-fleet-to-box"
}
import {
  to = module.network.google_compute_address.box
  id = "projects/example-project/regions/us-central1/addresses/pinecall-runtime-ip"
}
import {
  to = module.box.google_compute_instance.this
  id = "projects/example-project/zones/us-central1-c/instances/pinecall-runtime"
}
import {
  to = module.replica.google_compute_instance.this
  id = "projects/example-project/zones/us-central1-c/instances/example-replica"
}
import {
  to = google_service_account.fleet
  id = "projects/example-project/serviceAccounts/pinecall-fleet@example-project.iam.gserviceaccount.com"
}
import {
  to = module.store.aws_s3_bucket.backups
  id = "pinecall-box-backups-000000000000"
}
import {
  to = module.store.aws_s3_bucket.recordings
  id = "pinecall-box-recordings-000000000000"
}
import {
  to = module.store.aws_s3_bucket_public_access_block.backups
  id = "pinecall-box-backups-000000000000"
}
import {
  to = module.store.aws_s3_bucket_public_access_block.recordings
  id = "pinecall-box-recordings-000000000000"
}
import {
  to = module.store.aws_s3_bucket_server_side_encryption_configuration.backups
  id = "pinecall-box-backups-000000000000"
}
import {
  to = module.store.aws_s3_bucket_server_side_encryption_configuration.recordings
  id = "pinecall-box-recordings-000000000000"
}
import {
  to = module.store.aws_s3_bucket_lifecycle_configuration.backups
  id = "pinecall-box-backups-000000000000"
}
import {
  to = module.store.aws_iam_user.box_store
  id = "pinecall-box-store"
}
import {
  to = module.store.aws_iam_user_policy.box_store
  id = "pinecall-box-store:box-store-buckets"
}
import {
  to = module.store.aws_iam_user.box_alerts
  id = "pinecall-box-alerts"
}
import {
  to = module.store.aws_iam_user_policy.box_alerts
  id = "pinecall-box-alerts:send-alerts-only"
}
import {
  to = module.store.aws_ses_domain_identity.mail
  id = "pinecall.io"
}
import {
  to = module.store.aws_ses_email_identity.addresses["ops@example.com"]
  id = "ops@example.com"
}
import {
  to = module.store.aws_ses_email_identity.addresses["info@pinecall.io"]
  id = "info@pinecall.io"
}
import {
  to = module.dns.aws_route53_record.box["box.pinecall.io"]
  id = "Z029073115B4TJUU26ND6_box.pinecall.io_A"
}
import {
  to = module.dns.aws_route53_record.box["sandbox.pinecall.io"]
  id = "Z029073115B4TJUU26ND6_sandbox.pinecall.io_A"
}
import {
  to = module.dns.aws_route53_record.box["notify.pinecall.io"]
  id = "Z029073115B4TJUU26ND6_notify.pinecall.io_A"
}
import {
  to = module.dns.aws_route53_record.box["billing.pinecall.io"]
  id = "Z029073115B4TJUU26ND6_billing.pinecall.io_A"
}

import {
  to = module.apps.google_compute_instance.this
  id = "projects/example-project/zones/us-central1-c/instances/pinecall-apps-1"
}
