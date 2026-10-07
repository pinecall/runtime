# Google Cloud's delivery of the box's alerts: each rule of charts/pinecall/alerts.yaml (the one
# copy, which a cluster with the Prometheus Operator evaluates itself) made a Cloud Monitoring
# policy on what Managed Prometheus collects (the charts' `monitoring.collector: gmp`), plus the
# name as the internet reaches it, an uptime check from Google's regions, which no rule file can
# say. Mailed by Google to the operator.

variable "name" {
  type        = string
  description = "The world-pair: staging, production."
}

variable "domain" {
  type        = string
  description = "The name the cluster is served at (PINECALL_DOMAIN), checked from outside."
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
  # charts/pinecall/alerts.yaml, the one copy of the rules: a project holds several clusters, so
  # each selector names this one, with no comma left before a brace.
  rules = {
    for rule in yamldecode(file("${path.module}/../../../charts/pinecall/alerts.yaml")).groups[0].rules :
    replace(lower(rule.alert), "/[^a-z0-9]+/", "-") => {
      title    = rule.alert
      query    = replace(replace(rule.expr, "$cluster", "cluster=\"${local.cluster}\","), ",}", "}")
      duration = rule["for"]
      says     = rule.annotations.summary
    }
  }
}

# The name from outside: the load balancer, its certificate and a gateway, as a browser reaches
# them. The door answers who the box is and needs no key.
resource "google_monitoring_uptime_check_config" "name" {
  display_name = "Pinecall ${var.name}: ${var.domain}"
  timeout      = "10s"
  period       = "60s"
  http_check {
    path         = "/.well-known/pinecall"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
  monitored_resource {
    type   = "uptime_url"
    labels = { host = var.domain }
  }
}

resource "google_monitoring_alert_policy" "name" {
  display_name = "Pinecall ${var.name}: ${var.domain} unreachable"
  combiner     = "OR"
  conditions {
    display_name = "${var.domain} unreachable"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.name.uptime_check_id}\" AND resource.type=\"uptime_url\""
      duration        = "120s"
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }
  documentation {
    content   = "${var.domain} fails from more than one of Google's regions: the load balancer, its certificate or every gateway."
    mime_type = "text/markdown"
  }
  notification_channels = [for channel in google_monitoring_notification_channel.emails : channel.id]
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
