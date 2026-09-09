# The box, installed: every file in this directory, and where systemd, podman and Caddy read it.
# A manifest and not a script — each rule is one file and its place — run by the deploy:
#
#   sudo make -C /opt/pinecall/app/runtime/infra/box install
#
# Idempotent by construction: `install` overwrites what changed and leaves what did not, sysusers
# and tmpfiles make only what is missing, and a reload of the fence and of systemd costs nothing.
# The containers are NOT restarted here — a deploy is the runtime's, and the media plane stays up
# through it; a changed .container takes effect on its next restart, which is yours to time.

SYSTEMD   = /etc/systemd/system
QUADLET   = /etc/containers/systemd
CREDSTORE = /etc/credstore.encrypted

UNITS      = $(wildcard *.service)
CONTAINERS = $(wildcard containers/*)

.PHONY: install
install:
	install -D -m 644 -t /etc/sysusers.d sysusers.d/pinecall.conf
	install -D -m 644 -t /etc/tmpfiles.d tmpfiles.d/pinecall.conf
	systemd-sysusers
	systemd-tmpfiles --create
	install -D -m 644 -t $(SYSTEMD) $(UNITS)
	install -D -m 644 -t $(QUADLET) $(CONTAINERS)
	install -D -m 644 -t /etc/pinecall livekit.yaml sip.yaml
	install -D -m 644 caddy/Caddyfile /etc/caddy/Caddyfile
	install -D -m 644 caddy/pinecall.conf $(SYSTEMD)/caddy.service.d/pinecall.conf
	install -m 644 nftables.conf /etc/nftables.conf
	systemctl reload-or-restart nftables
	systemctl daemon-reload
	systemctl enable -q nftables caddy pinecall-secrets pinecall-postgres-image \
	    pinecall-redis pinecall-livekit pinecall-sip pinecall-postgres \
	    pinecall-gateway pinecall-worker-key pinecall-operator-key pinecall-worker
