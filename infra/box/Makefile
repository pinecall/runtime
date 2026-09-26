# The box, installed: every file in this directory, and where systemd, podman and Caddy read it.
# A manifest and not a script — each rule is one file and its place — run by the deploy in two
# halves, with the virtualenv synced between them because the second one runs the runtime's verbs:
#
#   sudo make -C /opt/pinecall/app/runtime/infra/box install     the files, where systemd reads them
#   sudo make -C /opt/pinecall/app/runtime/infra/box converge    the instances, then what is enabled
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
# The long-running units of the runtime, each given hardening.conf as a drop-in: what a process
# may touch (the file says what, and why the oneshots and the tenant's app are not in the list).
HARDENED   = pinecall-gateway@ pinecall-worker@ pinecall-overflow pinecall-fleet
# Every container unit but the embedder's. TEI is the one a box may not want — 2.3 GB of weights
# for a service two of the three providers do over HTTP — so the manifest, and not the unit,
# decides whether Quadlet ever sees its file: `embedder-tei` and `embedder-elsewhere` below.
TEI        = containers/pinecall-tei.container containers/pinecall-tei.volume
CONTAINERS = $(filter-out $(TEI),$(wildcard containers/*))

# The packages a box runs on, the same list cloud-init.yaml installs at first boot — a test pins
# the two equal — so a box born before a package was added converges on the next deploy.
# nodejs is what a tenant's app runs on (pinecall-app@.service): NodeSource's 24, because the framework
# says `>=24` and Ubuntu's own is 18 — `node-repo` below puts the vendor's repository in first.
PACKAGES = podman caddy nftables make curl nodejs

.PHONY: install converge packages node-repo pnpm doctor migrate-post providers peers
install: packages pnpm
	install -D -m 644 -t /etc/sysusers.d sysusers.d/pinecall.conf
	install -D -m 644 -t /etc/tmpfiles.d tmpfiles.d/pinecall.conf
	systemd-sysusers
	systemd-tmpfiles --create
	install -D -m 644 -t $(SYSTEMD) $(UNITS)
	$(foreach unit,$(HARDENED),install -D -m 644 hardening.conf $(SYSTEMD)/$(unit).service.d/hardening.conf &&) true
	$(MAKE) --no-print-directory journal
	install -D -m 644 -t $(QUADLET) $(CONTAINERS)
	$(MAKE) --no-print-directory embedder-$(EMBEDDER)
	install -D -m 644 -t /etc/pinecall livekit.yaml sip.yaml egress.yaml
	install -m 644 nftables.conf /etc/nftables.conf
	systemctl reload-or-restart nftables
	systemctl daemon-reload
	$(if $(FLEET_CLOUD),$(MAKE) --no-print-directory cloud-cli-$(FLEET_CLOUD),)

# The second half, once the virtualenv answers: every instance box.env lists is made whole — its env
# file and its credstore — or the deploy stops HERE, before one unit was enabled or one site moved,
# so a box never comes out of a deploy unable to start. Then Caddy's sites, the single units the
# templates replaced, and what the role enables.
converge:
	$(if $(filter worker,$(ROLE)),,systemctl start pinecall-secrets)
	$(if $(and $(filter production,$(INSTANCES)),$(filter-out worker,$(ROLE))),$(MAKE) --no-print-directory production-from-box-env)
	$(MAKE) --no-print-directory $(addprefix ready-,$(INSTANCES))
	$(if $(filter worker,$(ROLE)),,$(MAKE) --no-print-directory peers)
	$(if $(filter worker,$(ROLE)),,$(MAKE) --no-print-directory sites)
	$(MAKE) --no-print-directory retire
	$(MAKE) --no-print-directory enable-$(ROLE)

# The journal's bounds (journald.conf.d/pinecall.conf), and journald restarted only on the deploy
# that changed them: a restart is cheap on systemd ≥ 254, and a box that has the file asks nothing.
.PHONY: journal
journal:
	@if ! cmp -s journald.conf.d/pinecall.conf /etc/systemd/journald.conf.d/pinecall.conf; then \
	  install -D -m 644 -t /etc/systemd/journald.conf.d journald.conf.d/pinecall.conf; \
	  systemctl restart systemd-journald; fi

# Only what is missing is installed, so a deploy on a box that has everything asks apt nothing.
packages: node-repo
	@missing=""; for package in $(PACKAGES); do \
	  dpkg -s $$package >/dev/null 2>&1 || missing="$$missing $$package"; done; \
	if [ -n "$$missing" ]; then echo "installing$$missing"; \
	  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $$missing >/dev/null; fi

# NodeSource's repository, the documented way and not its setup script: the keyring, the one
# line, and an apt update only the first time. cloud-init.yaml does the same at birth, in bootcmd,
# so `nodejs` in PACKAGES means the same version on a box born today and a box born before. The
# key that came down the wire is held to the fingerprint NodeSource publishes — a test pins the
# two files to the same one — or it is thrown away and the deploy stops here.
NODE_MAJOR       = 24
NODE_KEYRING     = /etc/apt/keyrings/nodesource.gpg
NODE_LIST        = /etc/apt/sources.list.d/nodesource.list
NODE_FINGERPRINT = 9FD3B784BC1C6FC31A8A0A1C1655A0AB68576280
node-repo:
	@if [ ! -f $(NODE_LIST) ]; then \
	  install -d -m 755 /etc/apt/keyrings; \
	  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o $(NODE_KEYRING); \
	  gpg --show-keys --with-colons $(NODE_KEYRING) | grep -q '^fpr:*$(NODE_FINGERPRINT):' \
	    || { rm -f $(NODE_KEYRING); echo "nodesource: the key is not $(NODE_FINGERPRINT)"; exit 1; }; \
	  echo "deb [signed-by=$(NODE_KEYRING)] https://deb.nodesource.com/node_$(NODE_MAJOR).x nodistro main" > $(NODE_LIST); \
	  apt-get update -qq >/dev/null; fi

# The package manager a tenant's lockfile is written by. Global, from npm, pinned to the major
# the lockfile format belongs to; a box that has it asks for nothing.
PNPM_MAJOR = 11
pnpm:
	@command -v pnpm >/dev/null && [ "$$(pnpm --version | cut -d. -f1)" = "$(PNPM_MAJOR)" ] \
	  || npm install -g -q pnpm@$(PNPM_MAJOR) >/dev/null

# As one instance's units see the box — `INSTANCE=`, production unless named: their user, box.env
# and then that instance's env file, the box's credentials by name and the instance's own by path,
# in a transient unit systemd tears down on exit. The two localhost URLs are the ones every .service
# sets, and the files after them win, as they do for the units. An instance's own names are left
# out of the box's store — production's first draw still sits there — so each is loaded ONCE, from
# the instance's store, and the doctor of a sandbox never reads production's ops key.
#
# A verb run any other way on a box reads a box that does not exist: the credentials are systemd's
# and are NOT in anybody's environment, so `providers` would say "no key" for every key the box
# holds — and a screen that says that is the bug this whole table was written against.
INSTANCE ?= production
INSTANCE_STORE = $(INSTANCES_DIR)/$(INSTANCE).credstore
AS_THE_UNITS_SEE_IT = systemd-run --quiet --pipe --wait --collect \
	  -p User=pinecall -p WorkingDirectory=/opt/pinecall/app \
	  -E LIVEKIT_URL=ws://127.0.0.1:7880 -E TEI_URL=http://127.0.0.1:8081 \
	  -p EnvironmentFile=/etc/pinecall/box.env -p EnvironmentFile=$(INSTANCES_DIR)/$(INSTANCE).env \
	  $(foreach credential,$(filter-out $(INSTANCE_SECRETS),$(notdir $(wildcard $(CREDSTORE)/*))),-p ImportCredential=$(credential)) \
	  $(foreach credential,$(notdir $(wildcard $(INSTANCE_STORE)/*)),-p LoadCredentialEncrypted=$(credential):$(INSTANCE_STORE)/$(credential))

# Every key that is set is knocked at its own vendor: a dead one fails the deploy here, and not
# a caller's first call.
# `make doctor MAIL_TO=you@example.com` also posts one test letter through the box's own mail.
doctor:
	$(AS_THE_UNITS_SEE_IT) $(RUNTIME) doctor $(if $(MAIL_TO),--mail-to $(MAIL_TO))

# The migrations never run at startup (a `.post.sql`: a backfill, a constraint validated, an index
# on a big table), applied by a person, as the units see the instance — its own database, its own
# DSN. The doctor names them when one is pending; an instance made after they landed has them all.
migrate-post:
	$(AS_THE_UNITS_SEE_IT) $(RUNTIME) migrate up --post

# Every vendor this build runs, and what each one still wants ON THIS BOX — a plugin, a key, or
# nothing. `make providers DOES=tts` narrows it. Reads the catalog and the credentials and asks
# nothing of anybody, so it answers while the gateway is down.
providers:
	$(AS_THE_UNITS_SEE_IT) $(RUNTIME) providers $(if $(DOES),--does $(DOES))

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
# when box.env names a cloud — a hub whose workers a person stands up runs no loop. Both are
# production's, one each; every other runtime unit is a template enabled once per instance.
HUB_UNITS        = caddy pinecall-secrets pinecall-operator-key pinecall-overflow
HUB_TEMPLATES    = pinecall-db pinecall-gateway pinecall-worker-key
WORKER_TEMPLATES = pinecall-worker
EACH_INSTANCE    = $(foreach template,$(1),$(foreach name,$(INSTANCES),$(template)@$(name)))
CONTAINERS_  = pinecall-redis pinecall-livekit pinecall-sip pinecall-postgres pinecall-egress
FLEET_CLOUD  = $(shell sed -n 's/^PINECALL_FLEET_CLOUD=//p' /etc/pinecall/box.env 2>/dev/null || true)
FLEET_UNIT   = $(if $(FLEET_CLOUD),pinecall-fleet,)

.PHONY: enable-all enable-hub enable-worker
enable-all:
	systemctl enable -q nftables $(HUB_UNITS) $(call EACH_INSTANCE,$(HUB_TEMPLATES) $(WORKER_TEMPLATES)) $(FLEET_UNIT)
	$(if $(FLEET_UNIT),,-systemctl disable -q --now pinecall-fleet 2>/dev/null)
enable-hub:
	systemctl enable -q nftables $(HUB_UNITS) $(call EACH_INSTANCE,$(HUB_TEMPLATES)) $(FLEET_UNIT)
	$(if $(FLEET_UNIT),,-systemctl disable -q --now pinecall-fleet 2>/dev/null)
	-systemctl disable -q --now $(call EACH_INSTANCE,$(WORKER_TEMPLATES)) 2>/dev/null
# A hub that becomes a worker gives up the media plane, and the containers are given up the way
# the embedder is: `systemctl disable` refuses a Quadlet-generated unit and refuses BEFORE it would
# have stopped anything, so one command for the .service units and a separate `stop` for the
# generated ones. Both in one line, and the media plane outlived the role that owned it.
enable-worker:
	systemctl enable -q nftables $(call EACH_INSTANCE,$(WORKER_TEMPLATES))
	-systemctl disable -q --now $(HUB_UNITS) $(call EACH_INSTANCE,$(HUB_TEMPLATES)) pinecall-fleet 2>/dev/null
	-systemctl stop -q $(CONTAINERS_) 2>/dev/null

# ── Instances ──────────────────────────────────────────────────────────────────
# An instance is one env file and one credstore under /etc/pinecall/instances/ (README.md, "An
# instance"), and box.env's PINECALL_INSTANCES names the ones this box runs — `production`, the one
# every box has always been, when it names none. Every unit above that ends in `@` is enabled once
# per name, and nothing in any unit file says which names there are.
INSTANCES_DIR = /etc/pinecall/instances
INSTANCES = $(shell sed -n 's/^PINECALL_INSTANCES=//p' /etc/pinecall/box.env 2>/dev/null | tr -d "\"'")
INSTANCES := $(if $(strip $(INSTANCES)),$(INSTANCES),production)

# What one instance holds alone, by path, out of its own store — every other credential is the
# box's and is imported by name. The units load exactly these; a test holds the two lists equal.
INSTANCE_SECRETS = DATABASE_URL PINECALL_OPS_KEY PINECALL_VAULT_KEY PINECALL_WORKER_KEY
# What must be there before the units of an instance are enabled at all, by role: a gateway cannot
# open without the first three, and a worker box has no gateway to mint its key — it is copied.
NEEDS_all    = DATABASE_URL PINECALL_OPS_KEY PINECALL_VAULT_KEY
NEEDS_hub    = $(NEEDS_all)
NEEDS_worker = PINECALL_WORKER_KEY
# The two lines of an instance's file a Caddy site is written from.
DOMAIN_OF = sed -n 's|^PINECALL_DOMAIN=||p' $(INSTANCES_DIR)/$(1).env
PORT_OF   = sed -n 's|^PINECALL_GATEWAY_URL=http://[^/]*:\([0-9][0-9]*\)/*$$|\1|p' $(INSTANCES_DIR)/$(1).env

# A box born with one instance, the day it gains the rest: production's env file written by the
# same verb any instance is, from what box.env says of it, and its four secrets COPIED — never
# moved — out of the box's credstore into its own, where its units read them. The originals stay:
# a rollback to a runtime before instances reads them there, and so does the notifier's unit. Each
# is decrypted once alone first, so a copy never encrypts the empty output of a decrypt that failed.
BOX_SAYS = $(shell sed -n 's/^$(1)=//p' /etc/pinecall/box.env 2>/dev/null)
.PHONY: production-from-box-env
production-from-box-env:
	@test -f $(INSTANCES_DIR)/production.env || $(RUNTIME) box instance production \
	  --world $(or $(call BOX_SAYS,PINECALL_WORLD),production) --domain "$(call BOX_SAYS,PINECALL_DOMAIN)" \
	  $(if $(call BOX_SAYS,PINECALL_FLEET),--fleet $(call BOX_SAYS,PINECALL_FLEET)) \
	  $(if $(call BOX_SAYS,PINECALL_IDENTITY_URL),--identity $(call BOX_SAYS,PINECALL_IDENTITY_URL)) \
	  $(if $(call BOX_SAYS,PINECALL_ELSEWHERE_URL),--elsewhere $(call BOX_SAYS,PINECALL_ELSEWHERE_URL)) \
  $(if $(call BOX_SAYS,PINECALL_SANDBOX_URL),--sandbox $(call BOX_SAYS,PINECALL_SANDBOX_URL)) \
	  $(if $(call BOX_SAYS,PINECALL_MAX_JOBS),--max-jobs $(call BOX_SAYS,PINECALL_MAX_JOBS)) \
	  $(if $(call BOX_SAYS,PINECALL_IDLE_PROCESSES),--idle-processes $(call BOX_SAYS,PINECALL_IDLE_PROCESSES))
	install -d -m 700 $(INSTANCES_DIR)/production.credstore
	@for name in $(INSTANCE_SECRETS); do \
	  from=$(CREDSTORE)/$$name; into=$(INSTANCES_DIR)/production.credstore/$$name; \
	  if [ -f $$from ] && [ ! -f $$into ]; then \
	    systemd-creds decrypt --name=$$name $$from - >/dev/null \
	      && systemd-creds decrypt --name=$$name $$from - \
	         | systemd-creds encrypt --with-key=auto --name=$$name - $$into \
	      && echo "  copied $$name into production's own store"; fi; \
	done

# What the root Makefile asks the box, over ssh, so that the list and the file are read in one place.
.PHONY: instances
instances:
	@echo $(INSTANCES)

# The refusal that keeps a deploy from leaving a box that cannot start: an instance is listed, so
# its file and the secrets its units load by path must be there, or nothing past this line runs.
ready-%:
	@test -f $(INSTANCES_DIR)/$*.env || { echo "instance $*: no $(INSTANCES_DIR)/$*.env — make instance NAME=$* WORLD=… DOMAIN=…"; exit 1; }
	@for name in $(NEEDS_$(ROLE)); do test -f $(INSTANCES_DIR)/$*.credstore/$$name \
	  || { echo "instance $*: no $$name in its store — pinecall-runtime box secrets --instance $*"; exit 1; }; done
	$(if $(filter worker,$(ROLE)),,@test -n "$$($(call DOMAIN_OF,$*))" -a -n "$$($(call PORT_OF,$*))" \
	  || { echo "instance $*: its file names no PINECALL_DOMAIN, or no port in PINECALL_GATEWAY_URL"; exit 1; })

# The two instances of a pair trust each other with one fleet key each (README.md, "Peers"): a
# production whose file names PINECALL_SANDBOX_URL, and the listed instance answering at that name.
# Minted HERE, in converge, and not in a unit: a pair is two instances where a template's %i is
# one, the key lands in the OTHER instance's store, and its gateway must then load it — which a
# drop-in the manifest writes decides, before `make restart` starts anything. The gateways asked
# are the ones already running, so the deploy that first brings a sandbox up mints nothing for it
# (said and skipped), and the next one does. `box peer` never rotates a key it finds.
#
# A key is loaded by PATH, and a LoadCredentialEncrypted= path that is missing fails the unit's
# start — so it is not in the template, where every instance on every box would need one: each
# gateway gets a drop-in naming the peer keys its store holds, and none when it holds none. Then
# the one refusal: a production naming a sandbox it holds no key of would ask nobody about a
# developer's phone and say nothing, so the deploy stops here, before anything moves.
PEER_SECRETS = PINECALL_SANDBOX_KEY PINECALL_PEER_KEY
SANDBOX_URL_OF = sed -n 's|^PINECALL_SANDBOX_URL=||p' $(INSTANCES_DIR)/$(1).env
peers:
	$(RUNTIME) box peer --among $(INSTANCES)
	@for name in $(INSTANCES); do store=$(INSTANCES_DIR)/$$name.credstore; \
	  if [ -n "$$($(call SANDBOX_URL_OF,$$name))" ] && [ ! -f $$store/PINECALL_SANDBOX_KEY ]; then \
	    echo "instance $$name names its sandbox and holds no PINECALL_SANDBOX_KEY — once that sandbox's gateway answers: make peer FROM=<the sandbox> INTO=$$name"; exit 1; fi; \
	  loads=""; for id in $(PEER_SECRETS); do test -f $$store/$$id && loads="$$loads\nLoadCredentialEncrypted=$$id:$$store/$$id"; done; \
	  dropin=$(SYSTEMD)/pinecall-gateway@$$name.service.d; \
	  if [ -n "$$loads" ]; then install -d -m 755 $$dropin; printf '[Service]%b\n' "$$loads" > $$dropin/peers.conf; \
	  else rm -f $$dropin/peers.conf; fi; done
	systemctl daemon-reload

# What the root Makefile asks the box, over ssh, when a pair's key crosses to another box.
.PHONY: peer-secrets
peer-secrets:
	@echo $(PEER_SECRETS)

# One Caddy site per instance, `<domain> { import pinecall <port> }`, in a directory the manifest
# owns whole — so a file there whose name box.env no longer lists is taken away — and never in
# conf.d, which holds sites that are not the runtime's (the notifier's). Then the two files a box of
# ONE name used to be served by go: the sandbox's second site and the drop-in that handed Caddy
# box.env, which nothing reads any more. A reload drops no connection.
CADDY_SITES = /etc/caddy/instances
.PHONY: sites
sites:
	install -D -m 644 caddy/Caddyfile /etc/caddy/Caddyfile
	install -d -m 755 $(CADDY_SITES)
	@for name in $(INSTANCES); do \
	  printf '# The instance %s, written by infra/box/Makefile from its env file.\n%s {\n\timport pinecall %s\n}\n' \
	    "$$name" "$$($(call DOMAIN_OF,$$name))" "$$($(call PORT_OF,$$name))" > $(CADDY_SITES)/$$name.caddy; done
	@for site in $(CADDY_SITES)/*.caddy; do name=$$(basename $$site .caddy); \
	  case " $(INSTANCES) " in *" $$name "*) ;; *) rm -f $$site; echo "  took $$name off Caddy";; esac; done
	rm -f /etc/caddy/conf.d/sandbox.caddy $(SYSTEMD)/caddy.service.d/pinecall.conf
	systemctl daemon-reload
	-systemctl reload-or-restart caddy 2>/dev/null

# The single units the templates replaced, on the first deploy that has instances: disabled and
# their files taken away, and NOT stopped — the root Makefile's `restart` stops each right before
# production's own starts, so the gap is a restart's and not a whole deploy's. Then the units of a
# name box.env no longer lists, disabled and stopped, as its Caddy site is taken away above.
RETIRED = pinecall-gateway pinecall-worker pinecall-worker-key
.PHONY: retire
retire:
	-systemctl disable -q $(RETIRED) 2>/dev/null
	rm -f $(foreach unit,$(RETIRED),$(SYSTEMD)/$(unit).service)
	@for wanted in $(SYSTEMD)/multi-user.target.wants/pinecall-*@*.service; do \
	  unit=$$(basename $$wanted .service); name=$${unit#*@}; \
	  case " $(HUB_TEMPLATES) $(WORKER_TEMPLATES) " in *" $${unit%@*} "*) ;; *) continue;; esac; \
	  case " $(INSTANCES) " in *" $$name "*) ;; *) echo "  $$name is no instance any more: $$unit stops"; \
	    systemctl disable -q --now $$unit;; esac; done
	systemctl daemon-reload

# The cloud's own CLI, for the loop, only on a hub whose box.env names a cloud: the vendor's apt
# repository for gcloud and awscli, the release binary for hcloud. Never the snap — snapd refuses
# a service user whose home is outside /home, which the box's is (/opt/pinecall). Installed once;
# a box that has it asks for nothing.
GOOGLE_KEYRING = /usr/share/keyrings/cloud.google.gpg
# hcloud is a release binary and not a package, so the tarball is held to the checksum its
# release publishes, per architecture: one that does not match is not installed.
HCLOUD_VERSION      = 1.50.0
HCLOUD_SHA256_amd64 = 6725a8a73d07964e7d8c6970f2dce9d93eb5bbb5b3cab40668a4dda5c7471c41
HCLOUD_SHA256_arm64 = 8f600792f43e62df871d8f7e2ed30a1b6d271b35fd1e2f7a744e15db74015804
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
	  arch=$$(dpkg --print-architecture); file=hcloud-linux-$$arch.tar.gz; \
	  case $$arch in amd64) sum=$(HCLOUD_SHA256_amd64);; arm64) sum=$(HCLOUD_SHA256_arm64);; *) echo "hcloud: no build for $$arch"; exit 1;; esac; \
	  cd /tmp && curl -fsSLO https://github.com/hetznercloud/cli/releases/download/v$(HCLOUD_VERSION)/$$file \
	  && echo "$$sum  $$file" | sha256sum -c --quiet && tar -xzf $$file -C /usr/local/bin hcloud; rm -f /tmp/$$file; }

# The embedder, the same shape as the role above: box.env declares it, `make install` converges on
# it, and a box that changes the line changes what it runs on its next deploy. `systemctl enable`
# refuses a Quadlet unit outright, so what decides whether the embedder comes up at boot is whether
# its file is under $(QUADLET) at all, and the [Install] section inside it does the rest.
#
# `stop`, and not `disable --now`: disabling is what systemd refuses on a generated unit, and it
# refuses before it would have stopped anything. So the container is stopped while its generated
# unit still exists to be stopped, and the files go after — BOTH of them, the container's and the
# volume's, and then a reload: a generator's unit whose file is gone sits in `systemctl` as
# `not-found` until systemd is told to look again, and `make status` printed it for days. The weights stay — `podman volume rm
# pinecall-tei` frees the 2.3 GB, and that is a person's to type, because a line changed in a
# config file must not throw data away.
.PHONY: embedder-tei embedder-elsewhere
embedder-tei:
	install -D -m 644 -t $(QUADLET) $(TEI)
embedder-elsewhere:
	-systemctl stop pinecall-tei pinecall-tei-volume 2>/dev/null
	rm -f $(addprefix $(QUADLET)/,$(notdir $(TEI)))
	systemctl daemon-reload
