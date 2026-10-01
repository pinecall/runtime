#!/usr/bin/env bash
# A second machine made a streaming replica of the box's Postgres, as root on it, from a copy of
# the box's /opt/pinecall/infra:
#   replica.sh join <box address>   the replication password read from stdin and sealed here,
#                   Postgres's image built, a base backup streamed from the box through its slot
#                   into the volume `pinecall-postgres`, and the standby started as the unit
#                   `pinecall-postgres`: the same container, image and volume a box runs, so
#                   `pinecall-runtime box failover` promotes it and `box up` makes this machine
#                   the box (docs/a-box-in-production.md)
# Run again on a volume that holds a database, it changes nothing but the sealed password.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "replica.sh runs as root" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
BOX="$HERE/../box"
STORE=/etc/credstore.encrypted
IMAGE=localhost/pinecall/postgres:17-pgvector0.8.6-pgtextsearch1.4.0
DATA=/var/lib/postgresql/data

case "${1:-}" in
join)
    [ -n "${2:-}" ] || { echo "replica.sh join <box address>, the replication password on stdin" >&2; exit 2; }
    primary="$2"
    # A machine made a minute ago has an empty package index: podman is not found until it is read.
    command -v podman >/dev/null || { DEBIAN_FRONTEND=noninteractive apt-get update -q &&
        DEBIAN_FRONTEND=noninteractive apt-get install -y -q podman; }
    # The box's directories name the deploy account (install.sh makes it there); a replica that is
    # promoted runs `box up` on them, so it needs the same account before tmpfiles reads them.
    id deploy >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin deploy
    install -D -m 0644 "$BOX/sysusers.d/pinecall.conf" /etc/sysusers.d/pinecall.conf
    install -D -m 0644 "$BOX/tmpfiles.d/pinecall.conf" /etc/tmpfiles.d/pinecall.conf
    systemd-sysusers
    systemd-tmpfiles --create /etc/tmpfiles.d/pinecall.conf
    # A password piped in without a newline still counts: read says no at EOF and has read it.
    IFS= read -r password || [ -n "$password" ]
    printf '%s' "$password" |
        systemd-creds encrypt --name=PINECALL_REPLICATION_PASSWORD - "$STORE/PINECALL_REPLICATION_PASSWORD"
    chmod 0600 "$STORE/PINECALL_REPLICATION_PASSWORD"
    install -m 0644 "$BOX/pinecall-postgres-image.service" /etc/systemd/system/
    install -d /etc/containers/systemd
    install -m 0644 "$BOX/containers/pinecall-postgres.volume" /etc/containers/systemd/
    install -m 0644 "$HERE/pinecall-postgres.container" /etc/containers/systemd/
    systemctl daemon-reload
    systemctl start pinecall-postgres-image.service
    podman volume exists pinecall-postgres || podman volume create pinecall-postgres >/dev/null
    # -R leaves standby.signal and primary_conninfo behind: the database starts as the box's standby.
    if ! podman run --rm -v pinecall-postgres:$DATA --entrypoint test "$IMAGE" -s "$DATA/PG_VERSION"; then
        PGPASSWORD="$password" podman run --rm -e PGPASSWORD -v pinecall-postgres:$DATA \
            --entrypoint bash "$IMAGE" -ec "
                pg_basebackup -h '$primary' -U replicator -D '$DATA' -X stream -S pinecall_replica -R
                chown -R postgres:postgres '$DATA' && chmod 0700 '$DATA'"
    fi
    unset password
    systemctl start pinecall-postgres
    for _ in $(seq 60); do podman healthcheck run pinecall-postgres >/dev/null 2>&1 && break; sleep 1; done
    podman exec pinecall-postgres psql -U pinecall -d pinecall -Atc \
        "SELECT 'streaming from ' || sender_host || ', received up to ' || latest_end_lsn FROM pg_stat_wal_receiver"
    ;;
*)
    echo "replica.sh join <box address>, the replication password on stdin" >&2; exit 2 ;;
esac
