output "group" {
  value = aws_autoscaling_group.workers.name
}

output "image" {
  value = data.aws_ami.worker.name
}
