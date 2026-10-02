#!/usr/bin/env bash
# A machine of the cell that runs one world's workers and nothing else, as root on it, from a copy
# of the box's /opt/pinecall/infra (the box keeps Postgres, Redis, LiveKit, SIP and the gateways):
#   worker.sh join <box address> <wheel or pinecall==version> <world> [calls]
#                   the credentials read as a tar from stdin (`primary.sh worker-credentials <world>`
#                   on the box, piped here) and sealed here; the runtime installed; one worker of
#                   that world's fleet holding `calls` at once (default: four per vCPU, where
#                   LiveKit's 0.7 line falls at ~2.9 calls a vCPU: docs/scaling.md)
#   worker.sh release <wheel or pinecall==version>
#                   the runtime replaced, the worker restarted once its calls drained
# On the box, before: `primary.sh allow-worker <this machine's address>`. A worker machine reaches
# the box's LiveKit and gateways and nothing else of it: no Postgres, no Redis.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "worker.sh runs as root" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
BOX="$HERE/../box"
STORE=/etc/credstore.encrypted
UV=/opt/pinecall/bin/uv
VENV=/opt/pinecall/venv

installed() {  # the wheel's path or pinecall==version, as release.sh takes it
    case "$1" in
        *.whl) echo "$1[voice]" ;;
        pinecall==*) echo "pinecall[voice]==${1#pinecall==}" ;;
        *) echo "a wheel's path or pinecall==<version>, not $1" >&2; exit 2 ;;
    esac
}

world_here() {  # the world whose worker this machine runs
    for unit in /etc/systemd/system/multi-user.target.wants/pinecall-worker@*.service; do
        [ -e "$unit" ] || continue
        basename "$unit" .service | sed 's/pinecall-worker@//'
        return
    done
    echo "no worker was joined on this machine: worker.sh join first" >&2
    exit 1
}

case "${1:-}" in
join)
    [ -n "${2:-}" ] && [ -n "${3:-}" ] && [ -n "${4:-}" ] || {
        echo "worker.sh join <box address> <wheel or pinecall==version> <world> [calls]" >&2; exit 2; }
    box="$2"
    package="$(installed "$3")"
    world="$4"
    calls="${5:-$((4 * $(nproc)))}"
    install -D -m 0644 "$BOX/sysusers.d/pinecall.conf" /etc/sysusers.d/pinecall.conf
    install -D -m 0644 "$BOX/tmpfiles.d/pinecall.conf" /etc/tmpfiles.d/pinecall.conf
    systemd-sysusers
    systemd-tmpfiles --create /etc/tmpfiles.d/pinecall.conf
    # The credentials, sealed here one by one and never on a disk in the clear past this block.
    install -d -m 0700 "$STORE"
    taken="$(mktemp -d)"
    trap 'rm -rf "$taken"' EXIT
    tar -C "$taken" -xf -
    for file in "$taken"/*; do
        name="$(basename "$file")"
        case "$name" in *.env) continue ;; esac
        systemd-creds encrypt --name="$name" "$file" "$STORE/$name"
        chmod 0600 "$STORE/$name"
    done
    # The box's names, its object store, the fleet's agent name and health port, and the calls.
    install -d /etc/pinecall
    install -m 0644 "$taken/box.env" /etc/pinecall/box.env
    install -m 0600 "$taken/backup.env" /etc/pinecall/backup.env
    { grep -v '^PINECALL_MAX_JOBS=' "$taken/fleet.env"; echo "PINECALL_MAX_JOBS=$calls"; } \
        > /etc/pinecall/fleet.env
    printf 'LIVEKIT_URL=ws://%s:7880\nPINECALL_GATEWAY_URL=http://%s:8088\n' "$box" "$box" \
        > /etc/pinecall/cell.env
    rm -rf "$taken"
    trap - EXIT
    [ -x "$VENV/bin/python" ] || "$UV" venv --python /usr/bin/python3.12 "$VENV"
    "$UV" pip install --quiet --python "$VENV/bin/python" --reinstall-package pinecall "$package"
    chown -R deploy:deploy "$VENV" 2>/dev/null || true
    install -m 0644 "$HERE/pinecall-worker@.service" /etc/systemd/system/
    install -d /etc/systemd/system/pinecall-worker@.service.d
    install -m 0644 "$BOX/hardening.conf" /etc/systemd/system/pinecall-worker@.service.d/hardening.conf
    systemctl daemon-reload
    # Ready once LiveKit registered it and the gateway heard it (Type=notify).
    systemctl enable --now "pinecall-worker@$world"
    # The fence: ssh from anyone, nothing else in.
    install -m 0644 "$HERE/worker.nft" /etc/nftables.conf
    systemctl enable --now nftables
    nft -f /etc/nftables.conf
    echo "a worker of the $world fleet on this machine, $calls calls at once, at the box $box"
    ;;
release)
    [ -n "${2:-}" ] || { echo "worker.sh release <wheel or pinecall==version>" >&2; exit 2; }
    world="$(world_here)"
    "$UV" pip install --quiet --python "$VENV/bin/python" --reinstall-package pinecall "$(installed "$2")"
    # The worker drains its calls before it stops; the fleet's other machines take the new ones.
    systemctl restart "pinecall-worker@$world"
    echo "released $2 on the $world worker"
    ;;
*)
    echo "worker.sh join <box address> <wheel or pinecall==version> <world> [calls] | release <wheel>" >&2
    exit 2 ;;
esac
