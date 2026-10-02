# The credentials a fleet machine runs on, on AWS: kept in Secrets Manager under the names
# Google Cloud's are kept under (modules/secrets), each world's worker role reading its own fleet
# key and the three the worlds share. Declared here with no value; the box writes the values
# (`pinecall-runtime cell publish-secrets`), so none is ever in the state.

locals {
  shared = ["livekit-api-key", "livekit-api-secret", "pinecall-s3-secret-access-key"]
  names  = concat([for world in var.worlds : "pinecall-fleet-key-${world}"], local.shared)
}

resource "aws_secretsmanager_secret" "credential" {
  for_each                = toset(local.names)
  name                    = each.value
  recovery_window_in_days = 7
  tags = {
    pinecall = "box"
  }
}

# What a fleet machine of a world acts as, through its instance profile.
resource "aws_iam_role" "worker" {
  for_each = toset(var.worlds)
  name     = "pinecall-worker-${each.value}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "worker_reads" {
  for_each = toset(var.worlds)
  name     = "pinecall-worker-reads"
  role     = aws_iam_role.worker[each.value].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "secretsmanager:GetSecretValue"
      Resource = [
        for name in concat(["pinecall-fleet-key-${each.value}"], local.shared) :
        aws_secretsmanager_secret.credential[name].arn
      ]
    }]
  })
}

resource "aws_iam_instance_profile" "worker" {
  for_each = toset(var.worlds)
  name     = "pinecall-worker-${each.value}"
  role     = aws_iam_role.worker[each.value].name
}

# The box compares before it writes (`publish-secrets` puts a value only where one differs).
resource "aws_iam_role_policy" "box_publishes" {
  for_each = toset(var.publisher_roles)
  name     = "pinecall-box-publishes"
  role     = each.value
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"]
      Resource = [for secret in aws_secretsmanager_secret.credential : secret.arn]
    }]
  })
}
