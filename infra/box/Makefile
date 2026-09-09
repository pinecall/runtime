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
	$(MAKE) --no-print-directory enable-$(ROLE)

# What a box runs is its ROLE, read from /etc/pinecall/box.env: `all` is one machine with
# everything (the default), `hub` is the control plane and the media plane with no worker, and
# `worker` is a worker alone, dialling a hub by URL. Each target enables its own units and
# disables the others', so a box that changes role changes it on the next deploy.
#
# Only what is a FILE in /etc/systemd/system is enabled by name. A container's unit is generated
# into /run by podman's Quadlet generator at every daemon-reload, and `systemctl enable` refuses
# a generated unit outright — the `[Install]` section inside each .container is what wants it
# at boot, and pinecall-postgres-image is pulled in by the Requires= of the one that needs it.
ROLE = $(shell sed -n 's/^PINECALL_ROLE=//p' /etc/pinecall/box.env 2>/dev/null || true)
ROLE := $(if $(ROLE),$(ROLE),all)

HUB_UNITS    = caddy pinecall-secrets pinecall-gateway pinecall-worker-key pinecall-operator-key
WORKER_UNITS = pinecall-worker
CONTAINERS_  = pinecall-redis pinecall-livekit pinecall-sip pinecall-postgres

.PHONY: enable-all enable-hub enable-worker
enable-all:
	systemctl enable -q nftables $(HUB_UNITS) $(WORKER_UNITS)
enable-hub:
	systemctl enable -q nftables $(HUB_UNITS)
	-systemctl disable -q --now $(WORKER_UNITS) 2>/dev/null
enable-worker:
	systemctl enable -q nftables $(WORKER_UNITS)
	-systemctl disable -q --now $(HUB_UNITS) $(CONTAINERS_) 2>/dev/null
