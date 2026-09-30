#!/usr/bin/env bash
# The box made from this directory, once and again idempotently, as root on the box:
#   install.sh <production>[,<sandbox>]   files to their places, the box's secrets drawn, the media plane up
#   install.sh secret <NAME>          one credential from stdin (SMTP, the sign-up key)
#   install.sh vault-add              appends a key read from stdin to PINECALL_VAULT_KEY, so rows
#                                     sealed under it (a restored database) open; the first key seals
# `make box` copies infra/ to /opt/pinecall/infra and runs the first form; so does
# `pinecall-runtime box up`, from the copy the package carries. Nothing is printed.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "install.sh runs as root" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
STORE=/etc/credstore.encrypted

sealed() {  # NAME, the value on stdin
    systemd-creds encrypt --name="$1" - "$STORE/$1"
    chmod 0600 "$STORE/$1"
}

case "${1:-}" in
secret)
    [ -n "${2:-}" ] || { echo "install.sh secret <NAME>, the value on stdin" >&2; exit 2; }
    sealed "$2"
    echo "$2 sealed; restart the gateway to read it"
    exit 0 ;;
vault-add)
    now="$(systemd-creds decrypt --name=PINECALL_VAULT_KEY "$STORE/PINECALL_VAULT_KEY" -)"
    # A key piped in without a newline still counts: read says no at EOF and has read it.
    IFS= read -r older || [ -n "$older" ]
    printf '%s,%s' "$now" "$older" | sealed PINECALL_VAULT_KEY
    echo "PINECALL_VAULT_KEY now opens what the added key sealed; restart the gateway"
    exit 0 ;;
""|-*)
    echo "install.sh <production>[,<sandbox>] | secret <NAME> | vault-add" >&2; exit 2 ;;
esac

DOMAINS="$1"
FIRST="${DOMAINS%%,*}"
# The second name is the sandbox's; a box of one name serves both worlds at it.
SECOND="${DOMAINS#*,}"; [ "$SECOND" = "$DOMAINS" ] && SECOND=""
# cloud-init makes the deploy account with an ssh key, for `make deploy`; a box made by
# `pinecall-runtime box up` releases as root and only needs the account to own the venv.
id deploy >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin deploy

# The users and directories first: everything below is owned by them.
install -D -m 0644 "$HERE/sysusers.d/pinecall.conf" /etc/sysusers.d/pinecall.conf
install -D -m 0644 "$HERE/tmpfiles.d/pinecall.conf" /etc/tmpfiles.d/pinecall.conf
systemd-sysusers
systemd-tmpfiles --create /etc/tmpfiles.d/pinecall.conf

# age encrypts the nightly backup to a public key whose private half is never on the box: this
# repository's is Pinecall's own; the package carries none, and `box up --backup-key` writes yours.
command -v age >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q age
[ -f "$HERE/backup.age.pub" ] && install -m 0644 "$HERE/backup.age.pub" /etc/pinecall/backup.age.pub

install -m 0644 "$HERE/nftables.conf" /etc/nftables.conf
nft -f /etc/nftables.conf
install -d /etc/systemd/journald.conf.d
install -m 0644 "$HERE/journald.conf.d/pinecall.conf" /etc/systemd/journald.conf.d/pinecall.conf

# What is this box's and not a secret: a name per world, and every name Caddy answers to.
cat > /etc/pinecall/box.env <<ENV
PINECALL_DOMAIN=$FIRST
PINECALL_SANDBOX_DOMAIN=$SECOND
PINECALL_DOMAINS=${DOMAINS//,/, }
LIVEKIT_PUBLIC_URL=wss://$FIRST
ENV
install -m 0644 "$HERE"/livekit.yaml "$HERE"/sip.yaml "$HERE"/egress.yaml /etc/pinecall/
install -m 0644 "$HERE"/fleets/*.env /etc/pinecall/fleets/

# The box's secrets, drawn here once and never printed; a re-run keeps what exists.
if [ ! -f "$STORE/LIVEKIT_API_KEY" ]; then
    key="API$(openssl rand -hex 6)"
    secret="$(openssl rand -base64 36 | tr -d '\n/+=' | cut -c1-43)"
    password="$(openssl rand -hex 24)"
    printf '%s' "$key" | sealed LIVEKIT_API_KEY
    printf '%s' "$secret" | sealed LIVEKIT_API_SECRET
    printf 'LIVEKIT_KEYS=%s: %s\nLIVEKIT_API_KEY=%s\nLIVEKIT_API_SECRET=%s\nPOSTGRES_PASSWORD=%s\n' \
        "$key" "$secret" "$key" "$secret" "$password" | sealed media.env
    printf 'postgresql://pinecall:%s@127.0.0.1:5432/pinecall' "$password" | sealed DATABASE_URL
    unset key secret password
fi
# A Fernet key is 32 bytes, url-safe base64.
[ -f "$STORE/PINECALL_VAULT_KEY" ] || openssl rand -base64 32 | tr -d '\n' | tr '+/' '-_' | sealed PINECALL_VAULT_KEY
[ -f "$STORE/PINECALL_OPS_KEY" ] || printf 'pc_ops_%s' "$(openssl rand -hex 24)" | sealed PINECALL_OPS_KEY

# The containers, Caddy, the runtime's units.
install -d /etc/containers/systemd
install -m 0644 "$HERE"/containers/* /etc/containers/systemd/
install -d /etc/caddy/conf.d
install -m 0644 "$HERE/caddy/Caddyfile" /etc/caddy/Caddyfile
install -d /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironmentFile=/etc/pinecall/box.env\n' > /etc/systemd/system/caddy.service.d/pinecall.conf
install -m 0644 "$HERE"/pinecall-*.service "$HERE"/pinecall-*.timer /etc/systemd/system/
for unit in pinecall-gateway pinecall-worker@ pinecall-overflow@ pinecall-migrate pinecall-retention; do
    install -d "/etc/systemd/system/$unit.service.d"
    install -m 0644 "$HERE/hardening.conf" "/etc/systemd/system/$unit.service.d/hardening.conf"
done
install -D -m 0644 "$HERE/polkit/50-pinecall-deploy.rules" /etc/polkit-1/rules.d/50-pinecall-deploy.rules
# The operator's verbs typed at this box's shell, with the box's settings and credentials.
install -m 0755 "$HERE/pinecall-runtime" /usr/local/bin/pinecall-runtime

systemctl daemon-reload
systemctl restart systemd-journald
systemctl start pinecall-postgres-image.service
systemctl start pinecall-redis pinecall-livekit pinecall-sip pinecall-egress pinecall-postgres
systemctl restart caddy
# Started by the first deploy, which brings the code they run.
systemctl enable pinecall-migrate pinecall-gateway pinecall-worker@production \
    pinecall-worker@sandbox pinecall-overflow@production
systemctl enable --now pinecall-retention.timer
# No key, no backup: an unencrypted dump of every call is not written anywhere.
if [ -f /etc/pinecall/backup.age.pub ]; then
    systemctl enable --now pinecall-backup.timer
else
    systemctl disable --now pinecall-backup.timer 2>/dev/null || true
fi
echo "the box stands at $DOMAINS (production $FIRST, sandbox ${SECOND:-$FIRST})"
