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
