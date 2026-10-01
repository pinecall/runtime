#!/usr/bin/env bash
# The cell's alerts (infra/cell/alerts.yaml) evaluated on the box and mailed, as root:
#   alerts.sh apply   on when /etc/pinecall/alerts.env names who is told and how, and the SMTP
#                     password is sealed (install.sh secret PINECALL_ALERTS_SMTP_PASSWORD); off
#                     otherwise. Prometheus scrapes every gateway's /metrics and Alertmanager
#                     mails what fires, both on 127.0.0.1 alone. install.sh runs it; so does the
#                     operator after editing alerts.env
#   alerts.sh test    a test alert mailed through the same path, to prove it arrives
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "alerts.sh runs as root" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV=/etc/pinecall/alerts.env
SECRET=/etc/credstore.encrypted/PINECALL_ALERTS_SMTP_PASSWORD
# Where systemd hands Alertmanager the sealed password, opened, for its own process alone.
PASSWORD=/run/credentials/prometheus-alertmanager.service/PINECALL_ALERTS_SMTP_PASSWORD
UNITS=(prometheus prometheus-alertmanager)

off() {
    systemctl disable --now "${UNITS[@]}" 2>/dev/null || true
    echo "alerts off"
}

gateways() {  # every gateway this box runs, as Prometheus targets
    systemctl list-units --plain --no-legend 'pinecall-gateway@*.service' |
        sed -n 's/^pinecall-gateway@\([0-9]*\)\.service.*/"127.0.0.1:\1"/p' | paste -sd, -
}

case "${1:-}" in
apply)
    [ -s "$ENV" ] && [ -s "$SECRET" ] || { off; exit 0; }
    . "$ENV"
    : "${PINECALL_ALERTS_TO:?}" "${PINECALL_ALERTS_FROM:?}" "${PINECALL_ALERTS_SMTP:?}" "${PINECALL_ALERTS_SMTP_USER:?}"
    command -v prometheus >/dev/null && command -v prometheus-alertmanager >/dev/null ||
        DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${UNITS[@]}"
    install -m 0644 "$HERE/../cell/alerts.yaml" /etc/prometheus/pinecall-alerts.yaml
    cat > /etc/prometheus/prometheus.yml <<EOF
global:
  scrape_interval: 15s
  evaluation_interval: 15s
rule_files: [/etc/prometheus/pinecall-alerts.yaml]
alerting:
  alertmanagers: [{static_configs: [{targets: ["127.0.0.1:9093"]}]}]
scrape_configs:
  - job_name: pinecall
    static_configs: [{targets: [$(gateways)]}]
EOF
    cat > /etc/prometheus/alertmanager.yml <<EOF
global:
  smtp_smarthost: "$PINECALL_ALERTS_SMTP"
  smtp_from: "$PINECALL_ALERTS_FROM"
  smtp_auth_username: "$PINECALL_ALERTS_SMTP_USER"
  smtp_auth_password_file: "$PASSWORD"
  smtp_require_tls: true
route:
  receiver: operator
  group_by: [alertname]
  repeat_interval: 4h
receivers:
  - name: operator
    email_configs: [{to: "$PINECALL_ALERTS_TO", send_resolved: true}]
EOF
    echo 'ARGS="--web.listen-address=127.0.0.1:9090 --storage.tsdb.retention.time=15d"' > /etc/default/prometheus
    # No cluster: one Alertmanager, and its gossip port closed.
    echo 'ARGS="--web.listen-address=127.0.0.1:9093 --cluster.listen-address="' > /etc/default/prometheus-alertmanager
    install -d /etc/systemd/system/prometheus-alertmanager.service.d
    printf '[Service]\nLoadCredentialEncrypted=PINECALL_ALERTS_SMTP_PASSWORD:%s\n' "$SECRET" \
        > /etc/systemd/system/prometheus-alertmanager.service.d/pinecall.conf
    promtool check config /etc/prometheus/prometheus.yml >/dev/null
    systemctl daemon-reload
    systemctl enable "${UNITS[@]}" >/dev/null 2>&1
    systemctl restart "${UNITS[@]}"
    echo "alerts on: $(gateways) evaluated, mailed to $PINECALL_ALERTS_TO"
    ;;
test)
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    curl -fsS -X POST -H 'Content-Type: application/json' http://127.0.0.1:9093/api/v2/alerts \
        -d "[{\"labels\":{\"alertname\":\"PinecallAlertsTest\"},\"annotations\":{\"summary\":\"a test from alerts.sh: the path to the operator works\"},\"startsAt\":\"$now\"}]"
    echo "test alert handed to Alertmanager; it mails within its group wait (30 s)"
    ;;
*)
    echo "alerts.sh apply | test" >&2; exit 2 ;;
esac
