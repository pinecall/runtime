# The alerts a cluster carries: the gateways' own series and Postgres's (charts/pinecall's and
# charts/postgres' PodMonitoring, read by Managed Prometheus) under PromQL, and the name as the
# internet reaches it (an uptime check from Google's regions), evaluated by Cloud Monitoring and
# mailed by Google to the operator. v1's box carried the first three in its own Prometheus
# (../infra-v1/cell/alerts.yaml); its replica's rule has no series here, a cluster of one Postgres.

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
    # No gateway answers its collector: every door, every call's log, the console.
    gateways-down = {
      title    = "gateways down"
      query    = "absent(up{cluster=\"${local.cluster}\", job=\"pinecall-gateway\"} == 1)"
      duration = "120s"
      says     = "No gateway answers: the doors and every call's log are down."
    }
    # A gateway's reaper or sweep stopped on an error it does not catch: unsealed calls count
    # against their org's limit, or rooms nobody opened are never offered again.
    loop-stopped = {
      title    = "a gateway's loop stopped"
      query    = "min by (loop) (pinecall_loop_running{cluster=\"${local.cluster}\"}) == 0"
      duration = "120s"
      says     = "A gateway's reaper or sweep has stopped: restart that gateway (kubectl rollout restart deploy/pinecall-gateway) and read its log for why."
    }
    # Postgres does not answer its own exporter, or its pod is gone.
    postgres-down = {
      title    = "postgres down"
      query    = "absent(cnpg_collector_up{cluster=\"${local.cluster}\"} == 1)"
      duration = "120s"
      says     = "Postgres does not answer: every gateway's writes wait, then fail."
    }
    # The archiver's last attempt failed (the -1 of a WAL that never failed is left out): the
    # bucket holds no new WAL, so a restore reaches no further than the last one that went.
    wal-not-archived = {
      title    = "WAL not archived"
      query    = "(cnpg_pg_stat_archiver_seconds_since_last_failure{cluster=\"${local.cluster}\"} >= 0) < cnpg_pg_stat_archiver_seconds_since_last_archival{cluster=\"${local.cluster}\"}"
      duration = "900s"
      says     = "Postgres has failed to archive its WAL for 15 minutes: the backups stop at the last segment that went, and the disk fills with the rest."
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
