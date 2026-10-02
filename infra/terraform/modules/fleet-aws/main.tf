# A world's fleet of worker machines on AWS: an Auto Scaling group from the AMI Packer builds,
# grown by target tracking on the fleet's calls (written to CloudWatch by the fleet loop:
# infra/fleet/aws-asg.py measure), healed by EC2's own checks. It only grows: the group would end
# a machine it removes itself, call or no call, so the loop cordons the one too many, waits for its
# calls to end, and terminates it out of the group (as modules/fleet-gcp on Google Cloud).

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
  # The measure divides the calls by the machines in service.
  enabled_metrics = ["GroupInServiceInstances"]
  # A machine holding calls is never moved for balance between zones.
  suspended_processes = ["AZRebalance"]

  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }

  # The policy owns the size, and the loop terminates out of it.
  lifecycle {
    ignore_changes = [desired_capacity]
  }
}

# ⌈calls ÷ calls_per_machine⌉ machines: calls per machine in service, or the calls themselves
# while none is, so the group grows from zero.
resource "aws_autoscaling_policy" "calls" {
  name                   = "pinecall-fleet-calls"
  autoscaling_group_name = aws_autoscaling_group.workers.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    target_value     = var.calls_per_machine
    disable_scale_in = true

    customized_metric_specification {
      metrics {
        id          = "per_machine"
        expression  = "IF(machines > 0, calls / machines, calls)"
        return_data = true
      }
      metrics {
        id          = "calls"
        return_data = false
        metric_stat {
          stat = "Maximum"
          metric {
            namespace   = "Pinecall"
            metric_name = "fleet_calls"
            dimensions {
              name  = "fleet"
              value = var.fleet
            }
          }
        }
      }
      metrics {
        id          = "machines"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "AWS/AutoScaling"
            metric_name = "GroupInServiceInstances"
            dimensions {
              name  = "AutoScalingGroupName"
              value = aws_autoscaling_group.workers.name
            }
          }
        }
      }
    }
  }
}
