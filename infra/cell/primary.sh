#!/usr/bin/env bash
# The box's side of a replica, as root on the box:
#   primary.sh allow <replica address>   the role `replicator` (its password drawn here once and
#                   sealed as PINECALL_REPLICATION_PASSWORD), the slot `pinecall_replica`, the
#                   replica's line in pg_hba.conf, Postgres published on this machine's address
#                   toward the replica and fenced to it alone; Postgres restarts once, a few seconds,
#                   the first time
#   primary.sh forget                     the slot dropped, the line, the fence and the publishing
#                   gone: a replica that is retired or was promoted holds no WAL on this disk
#   primary.sh allow-gateway <address>    a gateway machine of this cell (infra/cell/gateway.sh):
#                   its pg_hba line for the role `pinecall`, Postgres, Redis and LiveKit's API
#                   published on this machine's address and fenced to the cell's gateway machines
#                   alone, and Caddy sending calls to it beside the box's own gateways
#   primary.sh forget-gateway <address>   all of that undone for one gateway machine
#   primary.sh gateway-credentials        what a gateway machine runs on (the box's credentials,
#                   its URLs pointed at this machine's address, and box.env) as a tar on stdout,
#                   for `gateway.sh join` on the other machine; refused onto a terminal
#   primary.sh allow-worker <address or range>   a worker machine of this cell (infra/cell/worker.sh),
#                   or the subnet the fleet loop makes them in (10.100.0.0/24): LiveKit's API and
#                   the gateways' balancer (8088) fenced to the cell's worker machines alone; no
#                   Postgres, no Redis
#   primary.sh forget-worker <address or range>   that undone
#   primary.sh worker-settings <world>    what a worker machine of that world's fleet runs on and is
#                   no secret (box.env, store.env, the fleet's env) as a tar on stdout, for
#                   `worker.sh image` on the machine the fleet's image is frozen from
#   primary.sh worker-credentials <world> what a worker machine of that world's fleet runs on (its
#                   fleet key, the LiveKit pair, the object store's secret, box.env, store.env,
#                   the fleet's env) as a tar on stdout, for `worker.sh join`; refused onto a
#                   terminal, and refused when recordings stay on this disk
# The password never crosses a terminal: `systemd-creds decrypt` on this box piped into
# `replica.sh join` on the other (docs/a-box-in-production.md).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "primary.sh runs as root" >&2; exit 2; }
STORE=/etc/credstore.encrypted
SLOT=pinecall_replica
# A replica that stops reading keeps at most this much WAL here; past it the slot is dropped by
# Postgres and the replica must join again. The box's disk is 30 GB.
KEPT=10GB
# The ports a container is published on beyond loopback, one file per container, a PublishPort=
# line each: install.sh appends them to the container it installs (podman 4.9's quadlet, Ubuntu
# 24.04's, reads no .container.d drop-ins), and so does this script, at once.
PUBLISHED=/etc/pinecall/published
CONTAINERS=/etc/containers/systemd
FENCE=/etc/pinecall/nftables.d/replica.nft
GATEWAYS=/etc/pinecall/nftables.d/gateways.nft
WORKERS=/etc/pinecall/nftables.d/workers.nft
REMOTE=/etc/pinecall/gateways.env
# Where a gateway machine's Caddy listens, on its own address, for the box's Caddy.
GATEWAY_PORT=8090
# What a gateway process opens: the box's own unit imports these, a gateway machine the same.
CREDENTIALS="DATABASE_URL PINECALL_REDIS_URL PINECALL_VAULT_KEY PINECALL_OPS_KEY LIVEKIT_API_KEY
LIVEKIT_API_SECRET PINECALL_SMTP_URL PINECALL_SIGNUP_KEY PINECALL_S3_SECRET_ACCESS_KEY"

sql() {
    podman exec pinecall-postgres psql -U pinecall -d pinecall -v ON_ERROR_STOP=1 -Atqc "$1"
}

hba() {  # the sed or grep program run on pg_hba.conf inside the container
    podman exec pinecall-postgres sh -c "$1"
}

with_published() {  # FILE KEPT: the container file, the kept lines after its loopback PublishPort=
    awk -v kept="$2" '{ print } /^PublishPort=127\.0\.0\.1:/ && !done {
        while ((getline line < kept) > 0) print line; done = 1 }' "$1"
}

published() {  # CONTAINER [ADDRESS:PORT:PORT]: published there too (none: on loopback alone)
    local kept="$PUBLISHED/$1.conf" installed="$CONTAINERS/$1.container" before
    install -d "$PUBLISHED"
    if [ -n "${2:-}" ]; then
        printf 'PublishPort=%s\n' "$2" >> "$kept"
        sort -u -o "$kept" "$kept"
    else
        rm -f "$kept"
    fi
    before="$(cat "$installed")"
    with_published "/opt/pinecall/infra/box/containers/$1.container" "$kept" > "$installed"
    [ "$(cat "$installed")" = "$before" ] && return
    systemctl daemon-reload
    systemctl restart "$1"
}

worker_settings_into() {  # DIR WORLD: what a worker machine runs on and is no secret
    cp /etc/pinecall/box.env "$1/box.env"
    cp /etc/pinecall/store.env "$1/store.env"
    cp "/etc/pinecall/fleets/$2.env" "$1/fleet.env"
}

remote() {  # ADDRESS:PORT add|remove: the gateway machines Caddy sends calls to, beside the box's own
    local kept
    kept="$(sed -n 's/^PINECALL_REMOTE_GATEWAYS=//p' "$REMOTE" 2>/dev/null | tr ' ' '\n' | grep -vxF "$1" | grep . || true)"
    [ "$2" = add ] && kept="$(printf '%s\n%s' "$kept" "$1" | grep . | sort -u)"
    printf 'PINECALL_REMOTE_GATEWAYS=%s\n' "$(echo $kept)" > "$REMOTE"
    systemctl restart caddy
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
    published pinecall-postgres "$here:5432:5432"
    echo "a replica may stream from $here:5432, from $replica alone; on it: replica.sh join $here"
    ;;
forget)
    sql "SELECT pg_drop_replication_slot('$SLOT') FROM pg_replication_slots WHERE slot_name = '$SLOT'" >/dev/null
    hba "sed -i '/^host replication replicator /d' \"\$PGDATA/pg_hba.conf\""
    sql "SELECT pg_reload_conf()" >/dev/null
    rm -f "$FENCE"
    nft -f /etc/nftables.conf
    # Postgres stays published while a gateway machine still reaches it.
    grep -qs "add element" "$GATEWAYS" || published pinecall-postgres
    echo "no replica streams from this box"
    ;;
allow-gateway)
    [ -n "${2:-}" ] || { echo "primary.sh allow-gateway <gateway machine address>" >&2; exit 2; }
    gateway="$2"
    here="$(ip -4 route get "$gateway" | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
    [ -n "$here" ] || { echo "no route from this machine to $gateway" >&2; exit 1; }
    line="host pinecall pinecall $gateway/32 scram-sha-256"
    hba "grep -qxF '$line' \"\$PGDATA/pg_hba.conf\" || echo '$line' >> \"\$PGDATA/pg_hba.conf\""
    sql "SELECT pg_reload_conf()" >/dev/null
    install -d /etc/pinecall/nftables.d
    printf 'add element inet pinecall gateways { %s }\n' "$gateway" >> "$GATEWAYS"
    sort -u -o "$GATEWAYS" "$GATEWAYS"
    nft -f /etc/nftables.conf
    # Published on this machine's address once, whichever machines are let in; the fence decides who.
    published "pinecall-postgres" "$here:5432:5432"
    published "pinecall-redis" "$here:6379:6379"
    published "pinecall-livekit" "$here:7880:7880"
    remote "$gateway:$GATEWAY_PORT" add
    echo "gateway machine $gateway may reach $here:5432, :6379 and :7880; Caddy sends it calls."
    echo "on it, from a copy of /opt/pinecall/infra: primary.sh gateway-credentials | gateway.sh join $here <wheel>"
    ;;
forget-gateway)
    [ -n "${2:-}" ] || { echo "primary.sh forget-gateway <gateway machine address>" >&2; exit 2; }
    gateway="$2"
    hba "sed -i '/^host pinecall pinecall ${gateway//./\\.}\\/32 /d' \"\$PGDATA/pg_hba.conf\""
    sql "SELECT pg_reload_conf()" >/dev/null
    [ -f "$GATEWAYS" ] && sed -i "/ $gateway }/d" "$GATEWAYS"
    nft -f /etc/nftables.conf
    remote "$gateway:$GATEWAY_PORT" remove
    echo "gateway machine $gateway is out of the cell"
    ;;
allow-worker)
    [ -n "${2:-}" ] || { echo "primary.sh allow-worker <worker machine address or range>" >&2; exit 2; }
    worker="$2"
    here="$(ip -4 route get "${worker%/*}" | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
    [ -n "$here" ] || { echo "no route from this machine to $worker" >&2; exit 1; }
    install -d /etc/pinecall/nftables.d
    printf 'add element inet pinecall workers { %s }\n' "$worker" >> "$WORKERS"
    sort -u -o "$WORKERS" "$WORKERS"
    nft -f /etc/nftables.conf
    # Published here by install.sh since 0.1.4; a box installed before runs LiveKit on loopback
    # alone until it restarts: once, said, SIP and egress with it.
    published "pinecall-livekit" "$here:7880:7880"
    if ! ss -Hltn "sport = :7880" | grep -q "$here:7880"; then
        echo "LiveKit restarts once to be published at $here (SIP and egress with it): a window"
        systemctl restart pinecall-livekit
    fi
    echo "worker machine $worker may reach $here:7880 and $here:8088."
    echo "on it, from a copy of /opt/pinecall/infra: primary.sh worker-credentials <world> | worker.sh join $here <wheel> <world>"
    ;;
forget-worker)
    [ -n "${2:-}" ] || { echo "primary.sh forget-worker <worker machine address or range>" >&2; exit 2; }
    worker="$2"
    [ -f "$WORKERS" ] && { grep -vF " $worker }" "$WORKERS" > "$WORKERS.new" || true; mv "$WORKERS.new" "$WORKERS"; }
    nft -f /etc/nftables.conf
    echo "worker machine $worker is out of the cell"
    ;;
worker-settings)
    world="${2:-}"
    [ -f "/etc/pinecall/fleets/$world.env" ] || {
        echo "primary.sh worker-settings <world>: production or sandbox" >&2; exit 2; }
    out="$(mktemp -d)"
    trap 'rm -rf "$out"' EXIT
    worker_settings_into "$out" "$world"
    tar -C "$out" -cf - .
    ;;
worker-credentials)
    [ -t 1 ] && { echo "worker-credentials writes secrets: pipe it into worker.sh join" >&2; exit 2; }
    world="${2:-}"
    [ -f "/etc/pinecall/fleets/$world.env" ] || {
        echo "primary.sh worker-credentials <world>: production or sandbox" >&2; exit 2; }
    # A recording stays on the disk of the machine that took the call; the box's gateways serve
    # only their own disk, so a worker machine needs the bucket.
    grep -qs '^PINECALL_RECORDINGS_BUCKET=.' /etc/pinecall/store.env || {
        echo "this box keeps recordings on its own disk: set PINECALL_RECORDINGS_BUCKET first (docs/a-box-in-production.md, \"Recordings, off the disk\")" >&2
        exit 1; }
    out="$(mktemp -d)"
    trap 'rm -rf "$out"' EXIT
    systemd-creds decrypt --name=PINECALL_WORKER_KEY \
        "/etc/pinecall/fleets/$world.credstore/PINECALL_WORKER_KEY" - > "$out/PINECALL_WORKER_KEY"
    for name in LIVEKIT_API_KEY LIVEKIT_API_SECRET PINECALL_S3_SECRET_ACCESS_KEY; do
        [ -f "$STORE/$name" ] || continue
        systemd-creds decrypt --name="$name" "$STORE/$name" - > "$out/$name"
    done
    worker_settings_into "$out" "$world"
    tar -C "$out" -cf - .
    ;;
gateway-credentials)
    [ -t 1 ] && { echo "gateway-credentials writes secrets: pipe it into gateway.sh join" >&2; exit 2; }
    here="$(ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
    out="$(mktemp -d)"
    trap 'rm -rf "$out"' EXIT
    for name in $CREDENTIALS; do
        [ -f "$STORE/$name" ] || continue
        systemd-creds decrypt --name="$name" "$STORE/$name" - |
            sed "s|@127.0.0.1:|@$here:|" > "$out/$name"
    done
    cp /etc/pinecall/box.env "$out/box.env"
    tar -C "$out" -cf - .
    ;;
*)
    echo "primary.sh allow <replica> | forget | allow-gateway <addr> | forget-gateway <addr> | gateway-credentials | allow-worker <addr> | forget-worker <addr> | worker-settings <world> | worker-credentials <world>" >&2
    exit 2 ;;
esac
