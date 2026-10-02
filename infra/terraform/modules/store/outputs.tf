output "backups_bucket" {
  value = aws_s3_bucket.backups.bucket
}

output "recordings_bucket" {
  value = aws_s3_bucket.recordings.bucket
}
