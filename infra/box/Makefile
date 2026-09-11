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
RUNTIME   = /opt/pinecall/venv/bin/pinecall-runtime

UNITS      = $(wildcard *.service)
# Every container unit but the embedder's. TEI is the one a box may not want — 2.3 GB of weights
# for a service two of the three providers do over HTTP — so the manifest, and not the unit,
# decides whether Quadlet ever sees its file: `embedder-tei` and `embedder-elsewhere` below.
TEI        = containers/pinecall-tei.container containers/pinecall-tei.volume
CONTAINERS = $(filter-out $(TEI),$(wildcard containers/*))

# The packages a box runs on, the same list cloud-init.yaml installs at first boot — a test pins
# the two equal — so a box born before a package was added converges on the next deploy.
# espeak-ng is the voice a simulated caller speaks with (`pinecall simulate --voice`).
PACKAGES = podman caddy nftables make curl espeak-ng

.PHONY: install packages doctor
install: packages
	install -D -m 644 -t /etc/sysusers.d sysusers.d/pinecall.conf
	install -D -m 644 -t /etc/tmpfiles.d tmpfiles.d/pinecall.conf
	systemd-sysusers
	systemd-tmpfiles --create
	install -D -m 644 -t $(SYSTEMD) $(UNITS)
	install -D -m 644 -t $(QUADLET) $(CONTAINERS)
	$(MAKE) --no-print-directory embedder-$(EMBEDDER)
	install -D -m 644 -t /etc/pinecall livekit.yaml sip.yaml
	install -D -m 644 caddy/Caddyfile /etc/caddy/Caddyfile
	install -D -m 644 caddy/pinecall.conf $(SYSTEMD)/caddy.service.d/pinecall.conf
	# Caddy re-reads its file on a reload and drops no connection doing it; without this line a
	# changed Caddyfile waits for the next restart, which nobody schedules.
	-systemctl reload-or-restart caddy 2>/dev/null
	install -m 644 nftables.conf /etc/nftables.conf
	systemctl reload-or-restart nftables
	systemctl daemon-reload
	$(if $(FLEET_CLOUD),$(MAKE) --no-print-directory cloud-cli-$(FLEET_CLOUD),)
	$(MAKE) --no-print-directory enable-$(ROLE)

# Only what is missing is installed, so a deploy on a box that has everything asks apt nothing.
packages:
	@missing=""; for package in $(PACKAGES); do \
	  dpkg -s $$package >/dev/null 2>&1 || missing="$$missing $$package"; done; \
	if [ -n "$$missing" ]; then echo "installing$$missing"; \
	  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $$missing >/dev/null; fi

# The doctor, as the units see the box: their user, their environment file, and every credential
# the credstore holds, in a transient unit systemd tears down on exit. The three localhost URLs are
# the ones each .service sets, and box.env overrides them on a worker as it does for the worker.
# Every key that is set is knocked at its own vendor: a dead one fails the deploy here, and not
# a caller's first call.
doctor:
	systemd-run --quiet --pipe --wait --collect \
	  -p User=pinecall -p WorkingDirectory=/opt/pinecall/app \
	  -E LIVEKIT_URL=ws://127.0.0.1:7880 -E PINECALL_GATEWAY_URL=http://127.0.0.1:8080 \
	  -E TEI_URL=http://127.0.0.1:8081 \
	  -p EnvironmentFile=/etc/pinecall/box.env \
	  $(foreach credential,$(notdir $(wildcard $(CREDSTORE)/*)),-p ImportCredential=$(credential)) \
	  $(RUNTIME) doctor

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

# Who embeds, out of the same file and by the same rule: `tei` — the default, and what a box.env
# with no such line means — is the container beside the other four; `perplexity` and `openrouter`
# are a vendor's HTTP door and a key in the credstore, so that box runs no embedder of its own.
# A WORKER embeds nothing whatever the line says: every lookup is run by the gateway, on the hub.
EMBED_PROVIDER = $(shell sed -n 's/^EMBED_PROVIDER=//p' /etc/pinecall/box.env 2>/dev/null || true)
EMBED_PROVIDER := $(if $(EMBED_PROVIDER),$(EMBED_PROVIDER),tei)

EMBEDDER := tei
ifneq ($(EMBED_PROVIDER),tei)
EMBEDDER := elsewhere
endif
ifeq ($(ROLE),worker)
EMBEDDER := elsewhere
endif

# The overflow agent is the hub's: it answers only when every real worker is full, so it lives
# where the media plane is and never counts as a seat. The fleet loop is the hub's too, and only
# when box.env names a cloud — a hub whose workers a person stands up runs no loop.
HUB_UNITS    = caddy pinecall-secrets pinecall-gateway pinecall-worker-key pinecall-operator-key pinecall-overflow
WORKER_UNITS = pinecall-worker
CONTAINERS_  = pinecall-redis pinecall-livekit pinecall-sip pinecall-postgres
FLEET_CLOUD  = $(shell sed -n 's/^PINECALL_FLEET_CLOUD=//p' /etc/pinecall/box.env 2>/dev/null || true)
FLEET_UNIT   = $(if $(FLEET_CLOUD),pinecall-fleet,)

.PHONY: enable-all enable-hub enable-worker
enable-all:
	systemctl enable -q nftables $(HUB_UNITS) $(WORKER_UNITS) $(FLEET_UNIT)
	$(if $(FLEET_UNIT),,-systemctl disable -q --now pinecall-fleet 2>/dev/null)
enable-hub:
	systemctl enable -q nftables $(HUB_UNITS) $(FLEET_UNIT)
	$(if $(FLEET_UNIT),,-systemctl disable -q --now pinecall-fleet 2>/dev/null)
	-systemctl disable -q --now $(WORKER_UNITS) 2>/dev/null
# A hub that becomes a worker gives up the media plane, and the containers are given up the way
# the embedder is: `systemctl disable` refuses a Quadlet-generated unit and refuses BEFORE it would
# have stopped anything, so one command for the .service units and a separate `stop` for the
# generated ones. Both in one line, and the media plane outlived the role that owned it.
enable-worker:
	systemctl enable -q nftables $(WORKER_UNITS)
	-systemctl disable -q --now $(HUB_UNITS) pinecall-fleet 2>/dev/null
	-systemctl stop -q $(CONTAINERS_) 2>/dev/null

# The cloud's own CLI, for the loop, only on a hub whose box.env names a cloud: the vendor's apt
# repository for gcloud and awscli, the release binary for hcloud. Never the snap — snapd refuses
# a service user whose home is outside /home, which the box's is (/opt/pinecall). Installed once;
# a box that has it asks for nothing.
GOOGLE_KEYRING = /usr/share/keyrings/cloud.google.gpg
HCLOUD_VERSION = 1.50.0
.PHONY: cloud-cli-gcp cloud-cli-aws cloud-cli-hetzner
cloud-cli-gcp:
	@dpkg -s google-cloud-cli >/dev/null 2>&1 || { echo "installing google-cloud-cli"; \
	  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor --yes -o $(GOOGLE_KEYRING); \
	  echo "deb [signed-by=$(GOOGLE_KEYRING)] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/google-cloud-sdk.list; \
	  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends google-cloud-cli >/dev/null; }
cloud-cli-aws:
	@dpkg -s awscli >/dev/null 2>&1 || { echo "installing awscli"; \
	  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends awscli >/dev/null; }
cloud-cli-hetzner:
	@test -x /usr/local/bin/hcloud || { echo "installing hcloud $(HCLOUD_VERSION)"; \
	  curl -fsSL https://github.com/hetznercloud/cli/releases/download/v$(HCLOUD_VERSION)/hcloud-linux-amd64.tar.gz | tar -xz -C /usr/local/bin hcloud; }

# The embedder, the same shape as the role above: box.env declares it, `make install` converges on
# it, and a box that changes the line changes what it runs on its next deploy. `systemctl enable`
# refuses a Quadlet unit outright, so what decides whether the embedder comes up at boot is whether
# its file is under $(QUADLET) at all, and the [Install] section inside it does the rest.
#
# `stop`, and not `disable --now`: disabling is what systemd refuses on a generated unit, and it
# refuses before it would have stopped anything. So the container is stopped while its generated
# unit still exists to be stopped, and the files go after. The weights stay — `podman volume rm
# pinecall-tei` frees the 2.3 GB, and that is a person's to type, because a line changed in a
# config file must not throw data away.
.PHONY: embedder-tei embedder-elsewhere
embedder-tei:
	install -D -m 644 -t $(QUADLET) $(TEI)
embedder-elsewhere:
	-systemctl stop pinecall-tei 2>/dev/null
	rm -f $(addprefix $(QUADLET)/,$(notdir $(TEI)))
