# The deploy, from a checkout to a box, with nothing but rsync, ssh, make and curl.
#
#   make deploy          sync the code, install the manifest, sync the environment, restart, check
#   make restart         the two processes, in order, with the health check between them
#   make logs UNIT=worker    follow one unit's journal: gateway (default) · worker · caddy
#   make status          every unit and container, one line each
#   make ssh             a shell on the box
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
# involved is the one that already has ssh. There is no build step — the gateway is an API and
# serves no page — so what travels is this repository and the wire beside it, Python and no more.
# Every command below is echoed as it runs, which is the whole point of make over a tool.

-include deploy.local.mk

BOX     ?=
DOMAIN  ?=
SSH_KEY ?=
REMOTE   = /opt/pinecall/app

SSH   = ssh $(if $(SSH_KEY),-i $(SSH_KEY)) -o BatchMode=yes -o ConnectTimeout=20 $(BOX)
RSYNC = rsync -az --delete -e "ssh $(if $(SSH_KEY),-i $(SSH_KEY)) -o BatchMode=yes" \
        --exclude .venv --exclude .env --exclude __pycache__ --exclude '*.pyc' \
        --exclude .pytest_cache --exclude .ruff_cache --exclude .mypy_cache --exclude .git

# The environment is built as the service user, exactly to uv.lock, with the extras both units
# run on. The units themselves never call uv: they run the virtualenv's own entrypoint.
UV_SYNC = sudo -u pinecall env UV_PROJECT_ENVIRONMENT=/opt/pinecall/venv UV_CACHE_DIR=/opt/pinecall/.cache/uv \
          /opt/pinecall/bin/uv sync -q --frozen --project $(REMOTE)/runtime --extra runtime

.PHONY: deploy sync install restart health status logs ssh require-box

deploy: sync install restart

# Two directories and no more: this repository, and the wire it is generated against. The wire is
# an editable path dependency (`../protocol/python`), so the checkout beside this one is what the
# lockfile resolves, on a laptop and on the box alike.
sync: require-box
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
	$(SSH) sudo systemctl restart pinecall-gateway
	$(MAKE) --no-print-directory health
	$(SSH) sudo systemctl restart pinecall-worker
	$(SSH) 'systemctl is-active pinecall-gateway pinecall-worker | paste -sd " "'

# Through Caddy, under the real certificate, so one request proves TLS, Caddy's upstream and the
# gateway at once. /openapi.json because it is the one door that answers WITHOUT a key: a check
# that carried one would fail for reasons that have nothing to do with the deploy.
health: require-box
	@for attempt in 1 2 3 4 5 6 7 8 9 10; do \
	  if curl -fsS -m 10 -o /dev/null https://$(DOMAIN)/openapi.json; then echo "healthy: https://$(DOMAIN)"; exit 0; fi; \
	  echo "  not yet ($$attempt/10)"; sleep 3; \
	done; echo "the gateway never answered https://$(DOMAIN)/openapi.json"; exit 1

status: require-box
	$(SSH) 'systemctl list-units "pinecall-*" nftables caddy --no-legend --plain | awk "{print \"  \" \$$1, \$$4}"; sudo podman ps --format "  {{.Names}}  {{.Status}}"'

UNIT ?= gateway
logs: require-box
	$(SSH) -t journalctl -u pinecall-$(UNIT) -f -o cat

ssh: require-box
	$(SSH)

require-box:
	@test -n "$(BOX)" -a -n "$(DOMAIN)" || { \
	  echo "which box? write deploy.local.mk beside this Makefile:"; \
	  echo "  BOX = user@address"; echo "  DOMAIN = box.example.com"; exit 2; }
