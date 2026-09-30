#!/bin/sh
# An Ubuntu 24.04 VM made the machine that runs the orgs' hosted apps: podman, gVisor, the fence,
# the runtime, and one runner unit per world named. Run as root; run again, it changes nothing.
#
#   install.sh <wheel or pinecall==version> production [sandbox]
#
# The runner key of each world is minted on the box (pinecall-runtime keys runner <world>) and
# sealed here with systemd-creds, never written in the clear: see README.md.
set -eu
PACKAGE=$1; shift
HERE=$(cd "$(dirname "$0")" && pwd)

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq podman nftables curl gnupg >/dev/null
if [ ! -f /usr/share/keyrings/gvisor-archive-keyring.gpg ]; then
  curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
fi
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
  > /etc/apt/sources.list.d/gvisor.list
apt-get update -qq
apt-get install -y -qq runsc >/dev/null
install -D -m 0644 "$HERE/runsc.conf" /etc/containers/containers.conf.d/runsc.conf

# --userns=auto hands each container a range of this user's ids; without one, every run fails
# "not enough unused IDs in user namespace".
id containers >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin containers
grep -q '^containers:' /etc/subuid || echo "containers:2147483647:2147483648" >> /etc/subuid
grep -q '^containers:' /etc/subgid || echo "containers:2147483647:2147483648" >> /etc/subgid

install -D -m 0644 "$HERE/fence.nft" /etc/nftables.d/pinecall-apps.nft
grep -q 'pinecall-apps.nft' /etc/nftables.conf || echo 'include "/etc/nftables.d/pinecall-apps.nft"' >> /etc/nftables.conf
systemctl enable --now nftables
nft -f /etc/nftables.d/pinecall-apps.nft 2>/dev/null || { nft delete table inet pinecall_apps; nft -f /etc/nftables.d/pinecall-apps.nft; }

command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
# A venv of its own: a machine that is also a box keeps the box's runtime where it is.
[ -d /opt/pinecall-runner/venv ] || uv venv --quiet --python 3.12 /opt/pinecall-runner/venv
uv pip install --quiet --python /opt/pinecall-runner/venv/bin/python "$PACKAGE"

podman pull -q docker.io/library/node:24-slim >/dev/null
install -D -m 0644 "$HERE/pinecall-runner@.service" /etc/systemd/system/pinecall-runner@.service
systemctl daemon-reload
for world in "$@"; do
  install -d -m 0755 "/var/lib/pinecall/runner/$world"
  install -d -m 0700 "/etc/pinecall/runner/$world.credstore"
  [ -f "/etc/pinecall/runner/$world.env" ] || echo "the gateway of $world is not set: /etc/pinecall/runner/$world.env" >&2
  if [ -f "/etc/pinecall/runner/$world.credstore/PINECALL_RUNNER_KEY" ]; then
    systemctl enable --now "pinecall-runner@$world"
    systemctl restart "pinecall-runner@$world"
  else
    echo "no runner key for $world yet: README.md, 'The key'" >&2
  fi
done
