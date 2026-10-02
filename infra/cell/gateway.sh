#!/usr/bin/env bash
# Operators run it as `sudo uvx --from pinecall==<version> pinecall-runtime cell join-gateway …`
# (join-gateway, release-gateway), which first copies the package's infra/ here.
# A machine of the cell that runs gateways and nothing else, as root on it, from a copy of the
# box's /opt/pinecall/infra (the box keeps Postgres, Redis, LiveKit and the workers):
#   gateway.sh join <box address> <wheel or pinecall==version> [processes]
#                   the box's credentials read as a tar from stdin (`primary.sh gateway-credentials`
#                   on the box, piped here) and sealed here; the runtime installed; `processes`
#                   gateways (default: one per two vCPUs) on loopback, and this machine's Caddy on
#                   its own address, port 8090, sending each call to one of them, fenced to the box
#   gateway.sh release <wheel or pinecall==version>
#                   the runtime replaced, its gateways restarted one at a time
# On the box, before: `primary.sh allow-gateway <this machine's address>`. Migrations run on the
# box alone (its release); a gateway machine never migrates.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "gateway.sh runs as root" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
BOX="$HERE/../box"
STORE=/etc/credstore.encrypted
UV=/opt/pinecall/bin/uv
VENV=/opt/pinecall/venv
PORT=8090
FIRST_GATEWAY=8080

installed() {  # the wheel's path or pinecall==version, as release.sh takes it
    case "$1" in
        *.whl) echo "$1[voice]" ;;
        pinecall==*) echo "pinecall[voice]==${1#pinecall==}" ;;
        *) echo "a wheel's path or pinecall==<version>, not $1" >&2; exit 2 ;;
    esac
}

gateways() {  # the instances this machine runs, by port
    for unit in /etc/systemd/system/multi-user.target.wants/pinecall-gateway@*.service; do
        [ -e "$unit" ] || continue
        basename "$unit" .service | sed 's/pinecall-gateway@//'
    done
}

answering() {
    for _ in $(seq 60); do curl -fs -o /dev/null "http://127.0.0.1:$1/" && return 0; sleep 1; done
    echo "the gateway on $1 did not answer in 60 s" >&2
    return 1
}

case "${1:-}" in
join)
    [ -n "${2:-}" ] && [ -n "${3:-}" ] || {
        echo "gateway.sh join <box address> <wheel or pinecall==version> [processes]" >&2; exit 2; }
    box="$2"
    package="$(installed "$3")"
    processes="${4:-$(( ($(nproc) + 1) / 2 ))}"
    here="$(ip -4 route get "$box" | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
    [ -n "$here" ] || { echo "no route from this machine to $box" >&2; exit 1; }
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
        [ "$name" = box.env ] && continue
        systemd-creds encrypt --name="$name" "$file" "$STORE/$name"
        chmod 0600 "$STORE/$name"
    done
    # The box's names and URLs; the pool sized to this machine, as install.sh sizes the box's.
    install -d /etc/pinecall
    grep -v '^PINECALL_DB_POOL=' "$taken/box.env" > /etc/pinecall/box.env
    echo "PINECALL_DB_POOL=$(( 2 * $(nproc) / processes + 2 ))" >> /etc/pinecall/box.env
    printf 'LIVEKIT_URL=ws://%s:7880\n' "$box" > /etc/pinecall/cell.env
    rm -rf "$taken"
    trap - EXIT
    [ -x "$VENV/bin/python" ] || "$UV" venv --python /usr/bin/python3.12 "$VENV"
    "$UV" pip install --quiet --python "$VENV/bin/python" --reinstall-package pinecall "$package"
    chown -R deploy:deploy "$VENV" 2>/dev/null || true
    install -m 0644 "$HERE/pinecall-gateway@.service" /etc/systemd/system/
    install -d /etc/systemd/system/pinecall-gateway@.service.d
    install -m 0644 "$BOX/hardening.conf" /etc/systemd/system/pinecall-gateway@.service.d/hardening.conf
    systemctl daemon-reload
    upstreams=""
    for i in $(seq 0 $((processes - 1))); do
        port=$((FIRST_GATEWAY + i))
        systemctl enable --now "pinecall-gateway@$port"
        answering "$port"
        upstreams="$upstreams 127.0.0.1:$port"
    done
    # This machine's Caddy: the box's Caddy hashed the call to this machine; here it is hashed
    # again to one process. The box's X-Forwarded-For is passed on as it came (the fence lets the
    # box alone reach this port; Ubuntu 24.04's Caddy has no `trusted_proxies`).
    cat > /etc/caddy/Caddyfile <<CADDY
http://:$PORT {
	bind $here
	reverse_proxy$upstreams {
		header_up X-Forwarded-For {http.request.header.X-Forwarded-For}
		lb_policy header Pinecall-Call
		lb_try_duration 10s
		lb_try_interval 250ms
		fail_duration 5s
		health_uri /
		health_interval 2s
	}
}
CADDY
    systemctl restart caddy
    # The fence: ssh from anyone, the gateways' port from the box alone.
    install -m 0644 "$HERE/gateway.nft" /etc/nftables.conf
    sed -i "s|@BOX@|$box|" /etc/nftables.conf
    systemctl enable --now nftables
    nft -f /etc/nftables.conf
    echo "$processes gateways on this machine, answering the box at $here:$PORT"
    ;;
release)
    [ -n "${2:-}" ] || { echo "gateway.sh release <wheel or pinecall==version>" >&2; exit 2; }
    "$UV" pip install --quiet --python "$VENV/bin/python" --reinstall-package pinecall "$(installed "$2")"
    for port in $(gateways); do
        systemctl restart "pinecall-gateway@$port"
        answering "$port"
    done
    echo "released $2 on $(gateways | wc -l | tr -d ' ') gateways"
    ;;
*)
    echo "gateway.sh join <box address> <wheel or pinecall==version> [processes] | release <wheel>" >&2
    exit 2 ;;
esac
