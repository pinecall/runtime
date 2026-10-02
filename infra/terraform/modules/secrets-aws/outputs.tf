output "worker_instance_profiles" {
  description = "Each world's worker instance profile, by world."
  value       = { for world, profile in aws_iam_instance_profile.worker : world => profile.name }
}
