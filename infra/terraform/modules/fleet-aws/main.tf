# A world's fleet of worker machines on AWS: an Auto Scaling group from the AMI Packer builds,
# healed by EC2's own checks, with no scaling policy. The fleet loop is the one thing that sizes
# it (infra/fleet/aws-asg.py): it raises the desired capacity when the fleet is busy over its
# target, and terminates a machine out of the group only once it is cordoned and its calls ended;
# the group would end a machine it removes itself, call or no call (as modules/fleet-gcp).

data "aws_ami" "worker" {
  owners      = ["self"]
  most_recent = true
  filter {
    name   = "name"
    values = ["pinecall-worker-${var.world}-*"]
  }
}

resource "aws_launch_template" "worker" {
  name_prefix            = "pinecall-worker-${var.world}-"
  image_id               = data.aws_ami.worker.id
  instance_type          = var.instance_type
  vpc_security_group_ids = var.security_groups

  iam_instance_profile {
    name = var.instance_profile
  }

  # IMDSv2 alone; the instance's tags readable from it, which is what `cell enroll` reads.
  metadata_options {
    http_tokens            = "required"
    instance_metadata_tags = "enabled"
  }

  # The machine's hostname is its instance id: the worker's name, and what the loop terminates.
  private_dns_name_options {
    hostname_type = "resource-name"
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      pinecall-cloud = "aws"
      pinecall-world = var.world
      pinecall       = "worker"
    }
  }
}

resource "aws_autoscaling_group" "workers" {
  name                = "pinecall-workers-${var.world}"
  min_size            = var.min
  max_size            = var.max
  vpc_zone_identifier = var.subnets
  health_check_type   = "EC2"
  # A machine holding calls is never moved for balance between zones.
  suspended_processes = ["AZRebalance"]

  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }

  # The loop owns the size: it raises it, and terminates out of it.
  lifecycle {
    ignore_changes = [desired_capacity]
  }
}
