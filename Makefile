# The deploy, from a checkout to a box, with nothing but rsync, ssh, make and curl.
#
#   make deploy          sync the code, install the manifest, sync the environment, restart, doctor
#   make restart         the two processes, in order, with the health check between them
#   make doctor          the runtime's doctor on the box, with the box's own credentials
#   make secret NAME=ELEVEN_API_KEY < the-key     one secret you bring, replaced in place
#   make logs UNIT=worker    follow one unit's journal: gateway (default) · worker · caddy
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

SSH   = ssh $(if $(SSH_KEY),-i $(SSH_KEY)) -o BatchMode=yes -o ConnectTimeout=20 $(BOX)
RSYNC = rsync -az --delete -e "ssh $(if $(SSH_KEY),-i $(SSH_KEY)) -o BatchMode=yes" \
        --exclude .venv --exclude .env --exclude __pycache__ --exclude '*.pyc' \
        --exclude .pytest_cache --exclude .ruff_cache --exclude .mypy_cache --exclude .git

# The environment is built as the service user, exactly to uv.lock, with the extras both units
# run on. The units themselves never call uv: they run the virtualenv's own entrypoint.
#
# `providers` is the other forty vendors livekit ships a plugin for (providers/catalog.py). They
# are thin HTTP clients and the box installs them all, because the alternative is a console that
# offers Cartesia and a call that answers "no plugin in this build" — and a redeploy is not a thing
# a tenant can do. `providers-big` is NOT here: boto3, the Azure speech SDK, the google-cloud
# clients and speechmatics' onnxruntime are a decision a box makes on purpose.
UV_SYNC = sudo -u pinecall env UV_PROJECT_ENVIRONMENT=/opt/pinecall/venv UV_CACHE_DIR=/opt/pinecall/.cache/uv \
          /opt/pinecall/bin/uv sync -q --frozen --project $(REMOTE)/runtime --extra runtime --extra providers

.PHONY: deploy console sync install restart restart-all restart-hub restart-worker health doctor providers secret status logs ssh require-box

deploy: console sync install restart doctor

# The console into src/pinecall/gateway/console, from the agents checkout beside this one (or
# PINECALL_AGENTS). The sync below carries it; the gateway serves it at `/`.
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

# The box's own manifest — infra/box/Makefile, every file and where systemd reads it — then the
# environment. A unit, a container or a fence the tree stopped describing cannot survive this.
install: require-box
	$(SSH) 'sudo make -s -C $(REMOTE)/runtime/infra/box install && $(UV_SYNC)'

# The gateway first, and the worker only once the gateway answers: a worker that registers with
# a gateway mid-restart is refused and retries, and the health check between the two is what
# keeps the phone from ringing into that gap. The containers are NOT restarted here — the media
# plane stays up through a deploy, and a changed .container is restarted between two calls, by you.
restart: require-box
	@case "$$($(SSH) sed -n 's/^PINECALL_ROLE=//p' /etc/pinecall/box.env)" in \
	  worker) $(MAKE) --no-print-directory restart-worker ;; \
	  hub)    $(MAKE) --no-print-directory restart-hub ;; \
	  *)      $(MAKE) --no-print-directory restart-all ;; \
	esac

# One machine with everything: the hub's two steps, then the worker it also runs.
restart-all: require-box restart-hub
	$(SSH) sudo systemctl restart pinecall-worker
	$(SSH) 'systemctl is-active pinecall-gateway pinecall-overflow pinecall-worker | paste -sd " "'

# A hub: the gateway, then the health check through Caddy — and NOT the worker: `systemctl
# restart` starts a unit the role disabled, and a hub that restarted its worker on every deploy
# would be one machine with everything again, quietly.
# The overflow agent restarts with the gateway it speaks to. The loop is enabled only on a hub
# whose box.env names a cloud — the manifest enables it and enabling starts nothing, so the first
# deploy that names a cloud STARTS it here, and every later one restarts it.
restart-hub: require-box
	# The box's own secrets first. `pinecall-secrets` is WantedBy=multi-user.target, so it runs at
	# BOOT and nowhere else — which is right on a machine cloud-init just made, and wrong on every
	# redeploy after: a box that has lost them sits there with a postgres that cannot read
	# `media.env` until somebody reboots it. Its condition makes this free when they are all there.
	$(SSH) sudo systemctl start pinecall-secrets
	# And the media plane, STARTED and never restarted: a Quadlet comes up from its own
	# [Install] at boot, so on a box that has not rebooted since the units were written it is
	# simply down — postgres answers only because the gateway requires it. `start` on something
	# already running is a no-op, which is what keeps this clear of the rule that a container is
	# never restarted under a call.
	$(SSH) 'sudo systemctl start pinecall-redis pinecall-livekit pinecall-sip pinecall-postgres 2>/dev/null || true'
	$(SSH) 'sudo systemctl restart pinecall-gateway pinecall-overflow && { systemctl is-enabled -q pinecall-fleet && sudo systemctl restart pinecall-fleet || true; }'
	$(MAKE) --no-print-directory health

# A worker alone: one unit, and the proof is the hub's SFU saying it registered, not a URL here.
restart-worker: require-box
	$(SSH) sudo systemctl restart pinecall-worker
	$(SSH) 'systemctl is-active pinecall-worker'

# Through Caddy, under the real certificate, so one request proves TLS, Caddy's upstream and the
# gateway at once. /openapi.json because it is the one door that answers WITHOUT a key: a check
# that carried one would fail for reasons that have nothing to do with the deploy.
health: require-box
	@for attempt in 1 2 3 4 5 6 7 8 9 10; do \
	  if curl -fsS -m 10 -o /dev/null https://$(DOMAIN)/openapi.json; then echo "healthy: https://$(DOMAIN)"; exit 0; fi; \
	  echo "  not yet ($$attempt/10)"; sleep 3; \
	done; echo "the gateway never answered https://$(DOMAIN)/openapi.json"; exit 1

# The last word of a deploy. The doctor runs on the box as the units run, with their credentials,
# and knocks at every vendor with the key the box holds: a key that expired, or was pasted wrong,
# fails the deploy here with its NAME on the screen — never its value — instead of failing the
# first caller. A worker box is asked after what a worker has; the hub after everything.
doctor: require-box
	$(SSH) sudo make -s -C $(REMOTE)/runtime/infra/box doctor

# Every vendor this build runs and what each one still wants on the box — a plugin, a key, or
# nothing. `make providers DOES=tts` narrows it. It reads the catalog and the box's own
# credentials, and never a key: the column says present or absent and no more.
providers: require-box
	$(SSH) sudo make -s -C $(REMOTE)/runtime/infra/box providers DOES=$(DOES)

# One secret you bring, replaced in place, the value on stdin and on no command line, no screen
# and no file in the clear; the same verb on a worker box with BOX= its address. A credential is
# read when a unit starts, so it is `make restart` that takes it.
#
#   printf '%s' "$$ELEVENLABS_API_KEY" | make secret NAME=ELEVEN_API_KEY
#
secret: require-box
	@test -n "$(NAME)" || { echo "which one? printf '%s' <the key> | make secret NAME=ELEVEN_API_KEY"; exit 2; }
	@$(SSH) sudo $(RUNTIME) box secret $(NAME)
	@echo "  read on the next start: make restart"

status: require-box
	$(SSH) 'systemctl list-units "pinecall-*" nftables caddy --no-legend --plain | awk "{print \"  \" \$$1, \$$4}"; sudo podman ps --format "  {{.Names}}  {{.Status}}"'

UNIT ?= gateway
logs: require-box
	$(SSH) -t journalctl -u pinecall-$(UNIT) -f -o cat

ssh: require-box
	$(SSH)

# A worker box's credentials, copied from the hub — the LiveKit keypair, the org's key the
# worker knocks with, the vendors' keys — and none it must not have: no DATABASE_URL, no ops
# key, no vault key. Each value goes hub → this laptop's pipe → worker, decrypted on one end
# and encrypted on the other by systemd, written to no file and printed on no screen.
#
#   make worker-secrets WORKER=deploy@203.0.113.9
#
WORKER_CREDENTIALS = LIVEKIT_API_KEY LIVEKIT_API_SECRET PINECALL_WORKER_KEY \
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

.PHONY: worker-secrets
worker-secrets: require-box
	@test -n "$(WORKER)" || { echo "which worker? make worker-secrets WORKER=user@address"; exit 2; }
	$(WSSH) sudo install -d -m 700 /etc/credstore.encrypted
	@for name in $(WORKER_CREDENTIALS); do \
	  $(SSH) sudo systemd-creds decrypt --name=$$name /etc/credstore.encrypted/$$name - \
	    | $(WSSH) sudo systemd-creds encrypt --with-key=auto --name=$$name - /etc/credstore.encrypted/$$name 2>/dev/null \
	    && echo "  kept $$name on $(WORKER)"; \
	done

require-box:
	@test -n "$(BOX)" -a -n "$(DOMAIN)" || { \
	  echo "which box? write deploy.local.mk beside this Makefile:"; \
	  echo "  BOX = user@address"; echo "  DOMAIN = box.example.com"; exit 2; }
