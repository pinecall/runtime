#!/usr/bin/env bash
# setup.sh — stand a fresh box up: docker, uv, the service user, the secrets, the media plane,
# Caddy and the firewall. Idempotent: everything it makes, it makes only if it is not there.
#
#   DOMAIN=box.example ./infra/box/setup.sh --dry-run   # print every step, change nothing
#   DOMAIN=box.example ./infra/box/setup.sh             # do it
#
# The keypair, the database password and the ops key are GENERATED ON THE BOX on first run and
# never leave it. Re-running keeps them: a script that rotates a secret by accident takes the
# phone line down and the log with it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=infra/box/remote.sh
. "$HERE/remote.sh"

reading_the_flags "$@"

if [ -z "${DOMAIN:-}" ]; then
  echo "DOMAIN is not set: Caddy needs the name this box answers to (DOMAIN=box.example $0)" >&2
  exit 2
fi

say "docker and uv, if they are not there yet"
on_the_box "command -v docker >/dev/null || { sudo apt-get update -qq && sudo apt-get install -y -qq docker.io docker-compose-v2; }"
on_the_box "sudo install -d -m 755 $TOOLS_DIR && { [ -x $UV ] || curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=$TOOLS_DIR UV_NO_MODIFY_PATH=1 sh -s -- -q; }"

say "the account the two units run as, and the directories each side owns"
on_the_box "id -u $SERVICE_USER >/dev/null 2>&1 || sudo useradd --system --create-home --home-dir /opt/pinecall --shell /usr/sbin/nologin $SERVICE_USER"
# The code arrives by rsync from a laptop, as the LOGIN account, so the login account owns the
# directory it lands in; the service user only reads it. The virtualenv is the other way round —
# `uv sync` writes it as the service user — which is why the two are separate directories and why
# UV_PROJECT_ENVIRONMENT points out of the tree. Nothing on this box can reach the repository.
on_the_box "sudo install -d -o \$(id -un) -g \$(id -un) $APP_DIR"
# .cache too: the service user's home is /opt/pinecall, which belongs to root, and uv's first act
# is to open a cache under it — "Failed to initialize cache at /opt/pinecall/.cache/uv", which
# reads like a uv problem and is a directory nobody made.
on_the_box "sudo install -d -o $SERVICE_USER -g $SERVICE_USER $VENV_DIR $MEDIA_DIR /opt/pinecall/.cache"
# The recordings are a customer's voice: their own directory, readable by nobody else.
on_the_box "sudo install -d -m 750 -o $SERVICE_USER -g $SERVICE_USER /var/lib/pinecall/recordings"

say "the secrets, generated once and kept forever"
# Two parsers read this file — systemd's EnvironmentFile and docker compose's .env — so it holds
# bare KEY=value lines: no quotes, no `export`, and no `$` in a value, which compose would expand.
on_the_box "$(
  cat <<REMOTE
sudo install -d -m 750 -o $SERVICE_USER -g $SERVICE_USER /etc/pinecall
# sudo test, not test: /etc/pinecall is 0750 and owned by the service user, so the login account
# running this script is REFUSED the file rather than told it is there — and a plain \`[ ! -f ]\`
# then answers "no such file" on a box that has one. That is the exact accident box.md swears
# this script will never have: every re-run rewrote the environment from scratch, rotating the
# LiveKit keypair, the database password and the ops key, and dropping every provider line a
# human had added. The Postgres volume remembers the password it was initialised with, so the
# box came back as \`InvalidPasswordError: password authentication failed for user "pinecall"\`
# from the gateway — a sentence about a password that names nothing that rotated it.
if ! sudo test -f $ENV_FILE; then
  password=\$(openssl rand -hex 16)
  {
    echo "LIVEKIT_API_KEY=API\$(openssl rand -hex 6)"
    echo "LIVEKIT_API_SECRET=\$(openssl rand -hex 32)"
    echo "LIVEKIT_URL=ws://127.0.0.1:7880"
    echo "POSTGRES_USER=pinecall"
    echo "POSTGRES_PASSWORD=\$password"
    echo "POSTGRES_DB=pinecall"
    echo "DATABASE_URL=postgresql://pinecall:\$password@127.0.0.1:5432/pinecall"
    echo "PINECALL_OPS_KEY=\$(openssl rand -hex 32)"
    echo "PINECALL_GATEWAY_URL=http://127.0.0.1:8080"
    echo "PINECALL_RECORDINGS=/var/lib/pinecall/recordings"
    echo "UV_PROJECT_ENVIRONMENT=$VENV_DIR"
  } | sudo tee $ENV_FILE >/dev/null
  sudo chown $SERVICE_USER:$SERVICE_USER $ENV_FILE && sudo chmod 600 $ENV_FILE
  echo "wrote $ENV_FILE — add the provider keys yourself; nothing else may"
else
  echo "$ENV_FILE is already there, and this script never rewrites a line it holds"
fi
# The secrets are written once. A PATH is not a secret and a box that predates one would run
# without it, so this one line is ensured on every run instead of being written only on the first.
# sudo, because the file is 0600 and owned by the service user: a plain grep here does not MISS the
# line, it is REFUSED the file, exits non-zero either way, and appends a duplicate on every run.
sudo grep -q '^UV_PROJECT_ENVIRONMENT=' $ENV_FILE ||
  echo "UV_PROJECT_ENVIRONMENT=$VENV_DIR" | sudo tee -a $ENV_FILE >/dev/null
REMOTE
)"

say "the media plane: the compose file, the two configs, and the Postgres image's own context"
ship_into "$MEDIA_DIR" "$HERE/box.yml" "$HERE/livekit.yaml" "$HERE/sip.yaml" "$HERE/../compose/postgres"
# compose reads `.env` beside its file; the box keeps one environment and both readers see it.
on_the_box "sudo ln -sfn $ENV_FILE $MEDIA_DIR/.env && sudo chown -h $SERVICE_USER:$SERVICE_USER $MEDIA_DIR/.env"

say "caddy: the only thing on this box that answers 443"
on_the_box "command -v caddy >/dev/null || { sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl && curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null && sudo apt-get update -qq && sudo apt-get install -y -qq caddy; }"
ship "$HERE/Caddyfile" "$HERE/caddy-domain.conf" "$BOX:/tmp/"
on_the_box "sudo mv /tmp/Caddyfile /etc/caddy/Caddyfile && echo PINECALL_DOMAIN=$DOMAIN | sudo tee /etc/default/caddy >/dev/null"
# Caddy's own unit reads no environment file, so the domain would never reach the Caddyfile.
on_the_box "sudo install -d /etc/systemd/system/caddy.service.d && sudo mv /tmp/caddy-domain.conf /etc/systemd/system/caddy.service.d/pinecall.conf && sudo systemctl daemon-reload && sudo systemctl restart caddy"

say "the firewall: 5060 to the carrier and to nobody else"
if [ "$DRY_RUN" = true ]; then
  "$HERE/firewall.sh" --dry-run
else
  "$HERE/firewall.sh"
fi

# Installed and enabled, not started: there is no code under them until the first `shipway deploy`,
# and shipway is what restarts them from then on. A unit is furniture; the code is the deploy.
install_the_unit pinecall-gateway "$HERE"
install_the_unit pinecall-worker "$HERE"

say "up"
on_the_box "cd $MEDIA_DIR && sudo docker compose -f box.yml up -d --wait --quiet-pull 2>&1 | tail -5"
on_the_box "sudo docker ps --format '   {{.Names}}  {{.Status}}'"

say "next"
echo "   the provider keys go in $ENV_FILE on the box, and nowhere else"
echo "   then, from the root of a checkout:  shipway deploy"
echo "   and once, on a box that has never run:  ./infra/box/first_run.sh"
