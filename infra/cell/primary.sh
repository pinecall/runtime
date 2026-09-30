#!/usr/bin/env bash
# The box's side of a replica, as root on the box:
#   primary.sh allow <replica address>   the role `replicator` (its password drawn here once and
#                   sealed as PINECALL_REPLICATION_PASSWORD), the slot `pinecall_replica`, the
#                   replica's line in pg_hba.conf, Postgres published on this machine's address
#                   toward the replica and fenced to it alone; Postgres restarts once, a few seconds,
#                   the first time
#   primary.sh forget                     the slot dropped, the line, the fence and the publishing
#                   gone: a replica that is retired or was promoted holds no WAL on this disk
# The password never crosses a terminal: `systemd-creds decrypt` on this box piped into
# `replica.sh join` on the other (docs/a-box-in-production.md).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "primary.sh runs as root" >&2; exit 2; }
STORE=/etc/credstore.encrypted
SLOT=pinecall_replica
# A replica that stops reading keeps at most this much WAL here; past it the slot is dropped by
# Postgres and the replica must join again. The box's disk is 30 GB.
KEPT=10GB
DROPIN=/etc/containers/systemd/pinecall-postgres.container.d/replica.conf
FENCE=/etc/pinecall/nftables.d/replica.nft

sql() {
    podman exec pinecall-postgres psql -U pinecall -d pinecall -v ON_ERROR_STOP=1 -Atqc "$1"
}

hba() {  # the sed or grep program run on pg_hba.conf inside the container
    podman exec pinecall-postgres sh -c "$1"
}

case "${1:-}" in
allow)
    [ -n "${2:-}" ] || { echo "primary.sh allow <replica address>" >&2; exit 2; }
    replica="$2"
    # The address of this machine on the way to the replica: where it will connect.
    here="$(ip -4 route get "$replica" | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
    [ -n "$here" ] || { echo "no route from this machine to $replica" >&2; exit 1; }
    if [ ! -f "$STORE/PINECALL_REPLICATION_PASSWORD" ]; then
        openssl rand -hex 24 | tr -d '\n' |
            systemd-creds encrypt --name=PINECALL_REPLICATION_PASSWORD - "$STORE/PINECALL_REPLICATION_PASSWORD"
        chmod 0600 "$STORE/PINECALL_REPLICATION_PASSWORD"
    fi
    sql "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'replicator') THEN
             CREATE ROLE replicator WITH REPLICATION LOGIN; END IF; END \$\$"
    # On stdin, so the password is on no command line.
    password="$(systemd-creds decrypt --name=PINECALL_REPLICATION_PASSWORD "$STORE/PINECALL_REPLICATION_PASSWORD" -)"
    printf "ALTER ROLE replicator PASSWORD '%s';\n" "$password" |
        podman exec -i pinecall-postgres psql -U pinecall -d pinecall -v ON_ERROR_STOP=1 -q
    unset password
    sql "SELECT pg_create_physical_replication_slot('$SLOT')
         WHERE NOT EXISTS (SELECT FROM pg_replication_slots WHERE slot_name = '$SLOT')" >/dev/null
    sql "ALTER SYSTEM SET max_slot_wal_keep_size = '$KEPT'"
    line="host replication replicator $replica/32 scram-sha-256"
    hba "grep -qxF '$line' \"\$PGDATA/pg_hba.conf\" || echo '$line' >> \"\$PGDATA/pg_hba.conf\""
    sql "SELECT pg_reload_conf()" >/dev/null
    install -d /etc/pinecall/nftables.d
    printf 'add element inet pinecall replicas { %s }\n' "$replica" > "$FENCE"
    nft -f /etc/nftables.conf
    published="$(printf '[Container]\nPublishPort=%s:5432:5432' "$here")"
    if [ "$(cat "$DROPIN" 2>/dev/null)" != "$published" ]; then
        install -d "$(dirname "$DROPIN")"
        printf '%s\n' "$published" > "$DROPIN"
        systemctl daemon-reload
        systemctl restart pinecall-postgres
    fi
    echo "a replica may stream from $here:5432, from $replica alone; on it: replica.sh join $here"
    ;;
forget)
    sql "SELECT pg_drop_replication_slot('$SLOT') FROM pg_replication_slots WHERE slot_name = '$SLOT'" >/dev/null
    hba "sed -i '/^host replication replicator /d' \"\$PGDATA/pg_hba.conf\""
    sql "SELECT pg_reload_conf()" >/dev/null
    rm -f "$FENCE"
    nft -f /etc/nftables.conf
    if [ -f "$DROPIN" ]; then
        rm -f "$DROPIN"
        systemctl daemon-reload
        systemctl restart pinecall-postgres
    fi
    echo "no replica streams from this box"
    ;;
*)
    echo "primary.sh allow <replica address> | forget" >&2; exit 2 ;;
esac
