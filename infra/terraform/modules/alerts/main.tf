# The alerts a cluster carries: the gateways' own series (charts/pinecall's PodMonitoring, read by
# Managed Prometheus) under PromQL, evaluated by Cloud Monitoring and mailed by Google to the
# operator. v1's box carried the same rules in its own Prometheus (../infra-v1/cell/alerts.yaml);
# its replica's rule has no series here, a cluster of one Postgres. Nothing else is added until one
# of these misses an incident.

variable "name" {
  type        = string
  description = "The world-pair: staging, production."
}

variable "emails" {
  type        = list(string)
  description = "Who is told."
}

resource "google_monitoring_notification_channel" "emails" {
  for_each     = toset(var.emails)
  display_name = "Pinecall ${var.name}: ${each.value}"
  type         = "email"
  labels       = { email_address = each.value }
}

locals {
  cluster = "pinecall-${var.name}"
  rules = {
    # A write that waits is every call waiting: the log is on the path of every entry.
    appends-slow = {
      title    = "appends slow"
      query    = "histogram_quantile(0.99, sum by (le) (rate(pinecall_append_seconds_bucket{cluster=\"${local.cluster}\"}[5m]))) > 0.25"
      duration = "300s"
      says     = "Appends take over 250 ms at p99: the database is the calls' bottleneck."
    }
    # Busy past 0.8 for five minutes and the burst has not caught up: new calls hear the overflow.
    fleet-busy = {
      title    = "fleet busy"
      query    = "sum by (fleet) (pinecall_fleet{what=\"busy\",cluster=\"${local.cluster}\"}) / sum by (fleet) (pinecall_fleet{what=\"seats\",cluster=\"${local.cluster}\"}) > 0.8"
      duration = "300s"
      says     = "A fleet is over 80 % busy: new calls are about to hear the overflow."
    }
    # A vendor over its error line: its calls are being stepped over to the fallbacks.
    vendor-failing = {
      title    = "vendor failing"
      query    = "max by (vendor) (pinecall_vendor_failing{cluster=\"${local.cluster}\"}) == 1"
      duration = "120s"
      says     = "A vendor fails half the calls handed it: they go to its fallbacks."
    }
  }
}

resource "google_monitoring_alert_policy" "rules" {
  for_each     = local.rules
  display_name = "Pinecall ${var.name}: ${each.value.title}"
  combiner     = "OR"
  conditions {
    display_name = each.value.title
    condition_prometheus_query_language {
      query               = each.value.query
      duration            = each.value.duration
      evaluation_interval = "60s"
    }
  }
  documentation {
    content   = each.value.says
    mime_type = "text/markdown"
  }
  notification_channels = [for channel in google_monitoring_notification_channel.emails : channel.id]
}
