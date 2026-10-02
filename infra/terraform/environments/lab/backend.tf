terraform {
  backend "gcs" {
    bucket = "pinecall-terraform-state-000000000000"
    prefix = "lab"
  }
}
