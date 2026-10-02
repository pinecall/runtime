terraform {
  backend "gcs" {
    bucket = "pinecall-terraform-state-209548925515"
    prefix = "lab"
  }
}
