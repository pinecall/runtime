# The deploy, from a checkout to a box, with nothing but rsync, ssh, make and curl.
#
#   make deploy          sync the code, install the manifest, sync the environment, restart, doctor
#   make restart         each instance's two processes, in order, with the health check between
#   make doctor          the runtime's doctor on the box, per instance, with its own credentials
#   make migrate-post INSTANCE=sandbox   the .post.sql migrations the doctor names, per instance
#   make instance NAME=sandbox WORLD=sandbox DOMAIN=sandbox.example.com IDENTITY=https://…
#                        one more instance of the runtime on this box: its env file and its secrets
#   make peer FROM=sandbox INTO=production    one instance's fleet key, kept in another's store
#   make secret NAME=ELEVEN_API_KEY < the-key     one secret you bring, replaced in place
#   make logs UNIT=worker@production    follow one unit's journal: gateway@<instance> (default
#                        gateway@production) · worker@<instance> · caddy
#   make status          every unit and container, one line each
#   make ssh             a shell on the box
#   make worker-secrets WORKER=user@address    a worker box's credentials, copied from the hub
#
# WHICH box is yours and not this repository's: put it in deploy.local.mk beside this file,
# which git ignores —
#
#   BOX    = deploy@203.0.113.7          # the account cloud-init made, at the machine
#   DOMAIN = box.example.com             # what Caddy answers to; the health check knocks here
#   SSH_KEY = ~/.ssh/id_ed25519          # optional; ssh's own default otherwise
#   EXTENSIONS_SRC = ../cloud            # optional; packages that plug a policy in (below)
#
# THE BOX HOLDS NO CREDENTIAL FOR THE REPOSITORY, deliberately (docs/decisions/box.md). It cannot
# clone and cannot fetch: the code is pushed to it by a person at a checkout, and the only account
# involved is the one that already has ssh. ONE build step, and it is the console: the gateway
# serves the page at `/`, so `scripts/console` builds the agents repo's bundle and copies it in as
# package data before the sync carries it. Everything else that travels is this repository and
# the wire beside it, Python and no more. Every command below is echoed as it runs, which is the
# whole point of make over a tool.

-include deploy.local.mk

BOX     ?=
DOMAIN  ?=
SSH_KEY ?=
REMOTE   = /opt/pinecall/app
RUNTIME  = /opt/pinecall/venv/bin/pinecall-runtime
MANIFEST = $(REMOTE)/runtime/infra/box

SSH   = ssh $(if $(SSH_KEY),-i $(SSH_KEY)) -o BatchMode=yes -o ConnectTimeout=20 $(BOX)
# What rsync leaves at home, named one by one and not `--filter=':- .gitignore'`: the console
# under src/pinecall/gateway/ is git-ignored and MUST travel, and a `!` line in a .gitignore means
# something else to rsync. What must not travel: the maintainer's notebook (docs/decisions/),
# the audio of real calls (recordings/), every .env and the file that names this box.
RSYNC = rsync -az --delete -e "ssh $(if $(SSH_KEY),-i $(SSH_KEY)) -o BatchMode=yes" \
        --exclude .git --exclude .venv --exclude __pycache__ --exclude '*.pyc' \
        --exclude .pytest_cache --exclude .ruff_cache --exclude .mypy_cache --exclude .hypothesis \
        --exclude .coverage --exclude '.coverage.*' --exclude htmlcov --exclude '*.egg-info' \
        --exclude build --exclude dist --exclude .DS_Store --exclude .idea --exclude .vscode \
        --exclude .env --exclude '.env.*' --exclude deploy.local.mk \
        --exclude docs/decisions --exclude recordings

# The environment is built as the service user, exactly to uv.lock, with the extras both units
# run on. The units themselves never call uv: they run the virtualenv's own entrypoint.
#
# `providers` is the other forty vendors livekit ships a plugin for (providers/catalog.py). They
# are thin HTTP clients and the box installs them all, because the alternative is a console that
# offers Cartesia and a call that answers "no plugin in this build" — and a redeploy is not a thing
# a tenant can do. `providers-big` is NOT here: boto3, the Azure speech SDK, the google-cloud
# clients and speechmatics' onnxruntime are a decision a box makes on purpose.
# --compile-bytecode: the units run under a read-only file system (infra/box/hardening.conf), so
# a .pyc Python would write on first import is written here, once, by the deploy.
UV_SYNC = sudo -u pinecall env UV_PROJECT_ENVIRONMENT=/opt/pinecall/venv UV_CACHE_DIR=/opt/pinecall/.cache/uv \
          /opt/pinecall/bin/uv sync -q --frozen --compile-bytecode --project $(REMOTE)/runtime --extra runtime --extra providers

# The packages beside the runtime that plug a policy into it — what a box that charges says its
# numbers with (docs/charging-for-it.md) — as checkouts on this machine, space separated. Each is
# carried to $(EXTENSIONS)/<its directory's name> and installed into the venv AFTER the sync, since
# `uv sync --frozen` removes whatever the lock does not name: installed once by hand, a package
# would be gone at the next deploy and a gateway told to load it would refuse to start. --no-deps:
# what a package depends on is pinecall-core, a member of the venv the sync just wrote, and a
# resolver asked for it here would go looking for it on PyPI. Which
# of them the gateway loads is PINECALL_EXTENSIONS in /etc/pinecall/box.env; unset here, nothing
# of this runs and a deploy is exactly what it was. --no-config: it runs as the service user from
# the deploy account's home, where uv would try to read that account's uv.toml and be refused.
EXTENSIONS_SRC ?=
EXTENSIONS      = /opt/pinecall/extensions
EXTENSION_DIRS  = $(foreach dir,$(EXTENSIONS_SRC),$(EXTENSIONS)/$(notdir $(abspath $(dir))))
UV_EXTENSIONS   = $(if $(EXTENSIONS_SRC),sudo -u pinecall env UV_CACHE_DIR=/opt/pinecall/.cache/uv \
                  /opt/pinecall/bin/uv pip install -q --no-config --no-deps --reinstall --compile-bytecode \
                  --python /opt/pinecall/venv/bin/python $(EXTENSION_DIRS) &&)

.PHONY: deploy console sync install restart restart-all restart-hub restart-worker health doctor migrate-post providers instance peer secret status logs ssh require-box

deploy: console sync install restart doctor

# The console into src/pinecall/gateway/console, from the console checkout beside this one (or
# PINECALL_CONSOLE). The sync below carries it; the gateway serves it at `/`.
console:
	scripts/console

# Two directories and no more: this repository, and the wire it is generated against. The wire is
# an editable path dependency (`../protocol/python`), so the checkout beside this one is what the
# lockfile resolves, on a laptop and on the box alike.
# `/opt/pinecall` is root's, so a plain mkdir by the deploy account fails with a Permission denied
# that says nothing about why. cloud-init makes $(REMOTE) at birth and hands it to that account —
# but a box that LOST it could then never be redeployed, which is exactly the state somebody
# reinstalling from zero is in. One idempotent line, the same ownership cloud-init gives it.
sync: require-box
	$(SSH) 'sudo install -d -o $$(id -un) -g $$(id -gn) -m 755 $(REMOTE)'
	$(SSH) mkdir -p $(REMOTE)/runtime $(REMOTE)/protocol/python
	$(RSYNC) ./ $(BOX):$(REMOTE)/runtime/
	$(RSYNC) ../protocol/python/ $(BOX):$(REMOTE)/protocol/python/
	$(if $(EXTENSIONS_SRC),$(SSH) 'sudo install -d -o $$(id -un) -g $$(id -gn) -m 755 $(EXTENSIONS)')
	$(if $(EXTENSIONS_SRC),$(foreach dir,$(EXTENSIONS_SRC),$(RSYNC) $(dir)/ $(BOX):$(EXTENSIONS)/$(notdir $(abspath $(dir)))/ &&) true)

# The box's own manifest — infra/box/Makefile, every file and where systemd reads it — then the
# environment, then the manifest's second half: every instance box.env lists made whole or the
# deploy stopped, then what the role enables. The second half runs the runtime's own verbs, which
# is why the environment is built between the two. A unit, a container or a fence the tree stopped
# describing cannot survive this.
install: require-box
	$(SSH) 'sudo make -s -C $(MANIFEST) install && $(UV_SYNC) && $(UV_EXTENSIONS) sudo make -s -C $(MANIFEST) converge'

# Instance by instance, in the order box.env lists them: its gateway first, and its worker only once
# the gateway answers — a worker that registers with a gateway mid-restart is refused and retries,
# and the health check between the two, through Caddy at THAT instance's name, is what keeps the
# phone from ringing into that gap. The containers are NOT restarted here — the media plane stays up
# through a deploy, and a changed .container is restarted between two calls, by you.
INSTANCES_ON_THE_BOX = $$($(SSH) make -s -C $(MANIFEST) instances)

restart: require-box
	@case "$$($(SSH) sed -n 's/^PINECALL_ROLE=//p' /etc/pinecall/box.env)" in \
	  worker) $(MAKE) --no-print-directory restart-worker ;; \
	  hub)    $(MAKE) --no-print-directory restart-hub ;; \
	  *)      $(MAKE) --no-print-directory restart-all ;; \
	esac

# One machine with everything: each instance's gateway, its health, its worker; then the hub's own.
restart-all: require-box the-media-plane
	@for name in $(INSTANCES_ON_THE_BOX); do \
	  $(MAKE) --no-print-directory restart-gateway-of restart-worker-of NAME=$$name || exit 1; done
	$(MAKE) --no-print-directory the-hubs-own

# A hub: each instance's gateway and its health check — and NOT its worker: `systemctl restart`
# starts a unit the role disabled, and a hub that restarted its workers on every deploy would be
# one machine with everything again, quietly.
restart-hub: require-box the-media-plane
	@for name in $(INSTANCES_ON_THE_BOX); do \
	  $(MAKE) --no-print-directory restart-gateway-of NAME=$$name || exit 1; done
	$(MAKE) --no-print-directory the-hubs-own

# A worker alone: one unit per instance, and the proof is the hub's SFU saying it registered.
restart-worker: require-box
	@for name in $(INSTANCES_ON_THE_BOX); do \
	  $(MAKE) --no-print-directory restart-worker-of NAME=$$name || exit 1; done

# The media plane, STARTED and never restarted: a Quadlet comes up from its own [Install] at boot,
# so on a box that has not rebooted since the units were written it is simply down — postgres
# answers only because the gateway requires it. `start` on something already running is a no-op,
# which is what keeps this clear of the rule that a container is never restarted under a call.
.PHONY: the-media-plane the-hubs-own restart-gateway-of restart-worker-of
the-media-plane: require-box
	$(SSH) 'sudo systemctl start pinecall-redis pinecall-livekit pinecall-sip pinecall-postgres pinecall-egress 2>/dev/null || true'

# One instance's database check and gateway, then its health at its own name. The single
# `pinecall-gateway` of a box born before instances is stopped first, the one time it still runs —
# it holds production's port — and that is a no-op on every deploy after (the manifest took its file).
restart-gateway-of: require-box
	$(SSH) 'sudo systemctl stop pinecall-gateway 2>/dev/null; sudo systemctl restart pinecall-db@$(NAME) pinecall-gateway@$(NAME)'
	@$(MAKE) --no-print-directory health DOMAIN=$$($(SSH) sed -n 's/^PINECALL_DOMAIN=//p' /etc/pinecall/instances/$(NAME).env)

# One instance's worker. The single `pinecall-worker` of a box born before instances drains first,
# the one time it still runs, so two workers of one fleet never overlap; a no-op after.
restart-worker-of: require-box
	$(SSH) 'sudo systemctl stop pinecall-worker 2>/dev/null; sudo systemctl restart pinecall-worker@$(NAME)'
	$(SSH) 'systemctl is-active pinecall-worker@$(NAME)'

# What a hub runs once, for production alone: the overflow agent restarts with the gateways it
# speaks to. The loop is enabled only on a hub whose box.env names a cloud — the manifest enables it
# and enabling starts nothing, so the first deploy that names a cloud STARTS it here, and every
# later one restarts it.
the-hubs-own: require-box
	$(SSH) 'sudo systemctl restart pinecall-overflow && { systemctl is-enabled -q pinecall-fleet && sudo systemctl restart pinecall-fleet || true; }'

# Through Caddy, under the real certificate, so one request proves TLS, Caddy's upstream and the
# gateway at once. /openapi.json because it is the one door that answers WITHOUT a key: a check
# that carried one would fail for reasons that have nothing to do with the deploy.
health: require-box
	@for attempt in 1 2 3 4 5 6 7 8 9 10; do \
	  if curl -fsS -m 10 -o /dev/null https://$(DOMAIN)/openapi.json; then echo "healthy: https://$(DOMAIN)"; exit 0; fi; \
	  echo "  not yet ($$attempt/10)"; sleep 3; \
	done; echo "the gateway never answered https://$(DOMAIN)/openapi.json"; exit 1

# The last word of a deploy. The doctor runs on the box as one instance's units run, with their
# credentials, and knocks at every vendor with the key the box holds: a key that expired, or was
# pasted wrong, fails the deploy here with its NAME on the screen — never its value — instead of
# failing the first caller. A worker box is asked after what a worker has; the hub after everything.
# `INSTANCE=sandbox` asks one; unnamed, every instance the box lists is asked, in its order.
doctor: require-box
	@for name in $(or $(INSTANCE),$(INSTANCES_ON_THE_BOX)); do \
	  $(SSH) sudo make -s -C $(MANIFEST) doctor INSTANCE=$$name MAIL_TO=$(MAIL_TO) || exit 1; done

# What the doctor says is pending — "post-deployment migration(s) not run" — applied, one instance
# (INSTANCE=sandbox) or every one the box lists. Never part of a deploy: a .post.sql can be long.
migrate-post: require-box
	@for name in $(or $(INSTANCE),$(INSTANCES_ON_THE_BOX)); do \
	  $(SSH) sudo make -s -C $(MANIFEST) migrate-post INSTANCE=$$name || exit 1; done

# Every vendor this build runs and what each one still wants on the box — a plugin, a key, or
# nothing. `make providers DOES=tts` narrows it. It reads the catalog and the box's own
# credentials, and never a key: the column says present or absent and no more.
providers: require-box
	$(SSH) sudo make -s -C $(MANIFEST) providers DOES=$(DOES)

# One more instance on this box, as root over ssh: its env file (`box instance`, which refuses a name
# that is not a slug, a port another instance holds, a sandbox with no IDENTITY, and a file that is
# already there) and its own secrets (`box secrets --instance`, never rewritten). Production's are
# the box's own and are never drawn here. Then its name goes into PINECALL_INSTANCES in
# /etc/pinecall/box.env — box.env is yours, and this writes no line of it — and `make deploy`.
#
#   make instance NAME=sandbox WORLD=sandbox DOMAIN=sandbox.example.com \
#                 IDENTITY=https://box.example.com ELSEWHERE=https://box.example.com
#
# Production names its sandbox the same way, once the sandbox is up — SANDBOX= is the URL it asks
# whose a ring is — and FORCE=1 writes over the file it already has:
#
#   make instance NAME=production WORLD=production DOMAIN=box.example.com \
#                 ELSEWHERE=https://sandbox.example.com SANDBOX=https://sandbox.example.com FORCE=1
#
instance: require-box
	@test -n "$(NAME)" -a -n "$(WORLD)" || { echo "which? make instance NAME=sandbox WORLD=sandbox DOMAIN=sandbox.example.com IDENTITY=https://…"; exit 2; }
	$(SSH) sudo $(RUNTIME) box instance $(NAME) --world $(WORLD) --domain $(DOMAIN) \
	  $(if $(PORT),--port $(PORT)) $(if $(IDENTITY),--identity $(IDENTITY)) $(if $(ELSEWHERE),--elsewhere $(ELSEWHERE)) \
	  $(if $(SANDBOX),--sandbox $(SANDBOX)) $(if $(FORCE),--force)
	$(if $(filter production,$(NAME)),,$(SSH) sudo $(RUNTIME) box secrets --instance $(NAME))

# One pair's key by hand. A pair on one box needs nothing of this — `make deploy` mints it — but a
# pair on two boxes does: the key is minted at FROM's gateway on BOX, into a store named for INTO
# there, then carried to INTO_BOX the way worker-secrets carries a key — decrypted on one end,
# encrypted on the other, through this laptop's pipe and no file — and taken off BOX. Then `make
# deploy` on INTO_BOX, whose manifest writes the gateway's drop-in that loads it.
#
#   make peer FROM=sandbox INTO=production                              both on BOX
#   make peer FROM=sandbox INTO=production INTO_BOX=deploy@203.0.113.9  INTO on another box
#
ISSH = ssh $(if $(SSH_KEY),-i $(SSH_KEY)) -o BatchMode=yes -o ConnectTimeout=20 $(INTO_BOX)
MINTED_INTO = /etc/pinecall/instances/$(INTO).credstore

peer: require-box
	@test -n "$(FROM)" -a -n "$(INTO)" || { echo "which? make peer FROM=sandbox INTO=production [INTO_BOX=…]"; exit 2; }
	$(SSH) sudo $(RUNTIME) box peer --from $(FROM) --into $(INTO) $(if $(FORCE),--force)
	$(if $(INTO_BOX),@for name in $$($(SSH) make -s -C $(MANIFEST) peer-secrets); do \
	  $(SSH) sudo test -f $(MINTED_INTO)/$$name || continue; \
	  $(ISSH) sudo install -d -m 700 $(MINTED_INTO); \
	  $(SSH) sudo systemd-creds decrypt --name=$$name $(MINTED_INTO)/$$name - \
	    | $(ISSH) sudo systemd-creds encrypt --with-key=auto --name=$$name - $(MINTED_INTO)/$$name \
	    && $(SSH) "sudo rm -f $(MINTED_INTO)/$$name; sudo rmdir --ignore-fail-on-non-empty $(MINTED_INTO)" \
	    && echo "  kept $$name in $(MINTED_INTO) on $(INTO_BOX)"; done)

# One secret you bring, replaced in place, the value on stdin and on no command line, no screen
# and no file in the clear; the same verb on a worker box with BOX= its address. A credential is
# read when a unit starts, so it is `make restart` that takes it. The box's store unless
# `INSTANCE=` names an instance, whose own store it goes into instead.
#
#   printf '%s' "$$ELEVENLABS_API_KEY" | make secret NAME=ELEVEN_API_KEY
#
secret: require-box
	@test -n "$(NAME)" || { echo "which one? printf '%s' <the key> | make secret NAME=ELEVEN_API_KEY"; exit 2; }
	@$(SSH) sudo $(RUNTIME) box secret $(NAME) $(if $(INSTANCE),--instance $(INSTANCE))
	@echo "  read on the next start: make restart"

status: require-box
	$(SSH) 'systemctl list-units "pinecall-*" nftables caddy --no-legend --plain | awk "{print \"  \" \$$1, \$$4}"; sudo podman ps --format "  {{.Names}}  {{.Status}}"'

UNIT ?= gateway@production
logs: require-box
	$(SSH) -t journalctl -u pinecall-$(UNIT) -f -o cat

ssh: require-box
	$(SSH)

# A worker box's credentials, copied from the hub — the LiveKit keypair and the vendors' keys out
# of the box's store, and the instance's fleet key out of that instance's own (INSTANCE=, production
# unless named) — and none it must not have: no DATABASE_URL, no ops key, no vault key. Each value
# goes hub → this laptop's pipe → worker, decrypted on one end and encrypted on the other by
# systemd, written to no file and printed on no screen. The instance's env file is not a secret and
# is not copied: a worker's names the HUB (infra/box/README.md, "An instance on a box of its own").
#
#   make worker-secrets WORKER=deploy@203.0.113.9
#
WORKER_CREDENTIALS = LIVEKIT_API_KEY LIVEKIT_API_SECRET \
                     ANTHROPIC_API_KEY ASSEMBLYAI_API_KEY ASYNCAI_API_KEY AZURE_SPEECH_KEY BASETEN_API_KEY \
                     BLAND_API_KEY CAMB_API_KEY CARTESIA_API_KEY CEREBRAS_API_KEY CLOVA_STT_SECRET_KEY \
                     DEEPGRAM_API_KEY ELEVEN_API_KEY FAL_KEY FIREWORKS_API_KEY FISH_API_KEY GLADIA_API_KEY \
                     GNANI_API_KEY GOOGLE_API_KEY GRADIUM_API_KEY GROQ_API_KEY HUME_API_KEY INWORLD_API_KEY \
                     LMNT_API_KEY MINIMAX_API_KEY MISTRAL_API_KEY MURF_API_KEY NEUPHONIC_API_KEY NVIDIA_API_KEY \
                     OPENAI_API_KEY PALABRA_API_KEY RESEMBLE_API_KEY RESPEECHER_API_KEY RIME_API_KEY \
                     SARVAM_API_KEY SIMPLISMART_API_KEY SLNG_API_KEY SMALLEST_API_KEY SONIOX_API_KEY \
                     SPEECHIFY_API_KEY SPEECHMATICS_API_KEY SPITCH_API_KEY UPLIFTAI_API_KEY VAKYAM_API_KEY \
                     XAI_API_KEY
WSSH = ssh $(if $(SSH_KEY),-i $(SSH_KEY)) -o BatchMode=yes -o ConnectTimeout=20 $(WORKER)
ITS_STORE = /etc/pinecall/instances/$(or $(INSTANCE),production).credstore

.PHONY: worker-secrets
worker-secrets: require-box
	@test -n "$(WORKER)" || { echo "which worker? make worker-secrets WORKER=user@address"; exit 2; }
	$(WSSH) sudo install -d -m 700 /etc/credstore.encrypted $(ITS_STORE)
	@for name in $(WORKER_CREDENTIALS); do \
	  $(SSH) sudo systemd-creds decrypt --name=$$name /etc/credstore.encrypted/$$name - \
	    | $(WSSH) sudo systemd-creds encrypt --with-key=auto --name=$$name - /etc/credstore.encrypted/$$name 2>/dev/null \
	    && echo "  kept $$name on $(WORKER)"; \
	done
	@$(SSH) sudo systemd-creds decrypt --name=PINECALL_WORKER_KEY $(ITS_STORE)/PINECALL_WORKER_KEY - \
	  | $(WSSH) sudo systemd-creds encrypt --with-key=auto --name=PINECALL_WORKER_KEY - $(ITS_STORE)/PINECALL_WORKER_KEY \
	  && echo "  kept PINECALL_WORKER_KEY in $(ITS_STORE) on $(WORKER)"

require-box:
	@test -n "$(BOX)" -a -n "$(DOMAIN)" || { \
	  echo "which box? write deploy.local.mk beside this Makefile:"; \
	  echo "  BOX = user@address"; echo "  DOMAIN = box.example.com"; exit 2; }
