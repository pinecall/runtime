# The object store the box keeps what leaves its disk in, and the mail its alerts go out by: two
# private buckets, the IAM user the box writes with, the one the alerts send as, and SES. An
# access key is never a resource here (its secret would be in the state): it is made by hand and
# sealed on the box (`install.sh secret PINECALL_S3_SECRET_ACCESS_KEY`).

resource "aws_s3_bucket" "backups" {
  bucket = var.backups_bucket
}

resource "aws_s3_bucket" "recordings" {
  bucket = var.recordings_bucket
  tags = {
    pinecall = "recordings"
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "recordings" {
  bucket                  = aws_s3_bucket.recordings.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "recordings" {
  bucket = aws_s3_bucket.recordings.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# The recordings bucket has no lifecycle: each org's retention_days is the rule, run by the box.
resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    id     = "forget-after-35-days"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 2
    }
    expiration {
      days = var.backups_kept_days
    }
  }
}

resource "aws_iam_user" "box_store" {
  name = "pinecall-box-store"
  tags = {
    purpose = "box.pinecall.io backups and WAL"
  }
}

resource "aws_iam_user_policy" "box_store" {
  name = "box-store-buckets"
  user = aws_iam_user.box_store.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.backups.arn, aws_s3_bucket.recordings.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = ["${aws_s3_bucket.backups.arn}/*", "${aws_s3_bucket.recordings.arn}/*"]
      },
    ]
  })
}

resource "aws_iam_user" "box_alerts" {
  name = "pinecall-box-alerts"
  tags = {
    purpose = "box-alert-mail"
  }
}

resource "aws_iam_user_policy" "box_alerts" {
  name = "send-alerts-only"
  user = aws_iam_user.box_alerts.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["ses:SendRawEmail", "ses:SendEmail"]
        Resource  = "*"
        Condition = { StringEquals = { "ses:FromAddress" = var.alerts_from } }
      },
    ]
  })
}

resource "aws_ses_domain_identity" "mail" {
  domain = var.mail_domain
}

resource "aws_ses_email_identity" "addresses" {
  for_each = toset(var.mail_addresses)
  email    = each.value
}
