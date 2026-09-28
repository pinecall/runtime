# check · test · db · hooks · box · deploy · rollback · logs · ssh · test-box.

# The new box's ssh alias; the old box (v1) is never a target of this file.
BOX      ?= example-box
DOMAINS  ?= sandbox.pinecall.io
TUNNEL   ?= 15432
WHEEL    ?= $(shell git rev-parse --short HEAD)$(shell git diff --quiet HEAD || echo -dirty)

# The laptop's Postgres, for the suites only: in colima, on tmpfs, thrown away with the container.
DB_IMAGE  = pinecall/postgres:17-pgvector0.8.6-pgtextsearch1.4.0
DB_NAME   = pinecall-test-postgres
DB_PORT  ?= 55432
LOCAL_DSN = postgresql://pinecall:pinecall@127.0.0.1:$(DB_PORT)/pinecall
T        ?= tests

check:            ## the rules and every suite that needs no database, on every core
	uv run pytest -q -n auto

test: db          ## every suite (or T=tests/log), on the local Postgres, on every core
	DATABASE_URL=$(LOCAL_DSN) uv run pytest -q -n auto $(T)

# Durability is off: a test database that loses its last second on a crash loses nothing.
db:               ## the local Postgres: colima up, the image built once, the container running
	@colima status >/dev/null 2>&1 || colima start
	@docker image inspect $(DB_IMAGE) >/dev/null 2>&1 || docker build -t $(DB_IMAGE) infra/postgres
	@docker inspect -f '{{.State.Running}}' $(DB_NAME) 2>/dev/null | grep -q true || { \
	  docker rm -f $(DB_NAME) >/dev/null 2>&1; \
	  docker run -d --name $(DB_NAME) -p 127.0.0.1:$(DB_PORT):5432 --tmpfs /var/lib/postgresql/data \
	    -e POSTGRES_USER=pinecall -e POSTGRES_PASSWORD=pinecall -e POSTGRES_DB=pinecall $(DB_IMAGE) \
	    -c fsync=off -c synchronous_commit=off -c full_page_writes=off >/dev/null; }
	@until docker exec $(DB_NAME) pg_isready -U pinecall -d pinecall >/dev/null 2>&1; do sleep 1; done

hooks:            ## the pre-commit hook: `make check`
	git config core.hooksPath .githooks

box:              ## infra/ to the box and install.sh run there: once, and after infra/box changes
	rsync -a --delete infra/ $(BOX):/tmp/pinecall-infra/
	ssh $(BOX) 'sudo rsync -a --delete /tmp/pinecall-infra/ /opt/pinecall/infra/ && sudo /opt/pinecall/infra/box/install.sh $(DOMAINS)'

deploy:           ## the console built in, a wheel, released on the box, the live suite, the journal
	scripts/console
	rm -rf dist && uv build --wheel --quiet
	ssh $(BOX) 'mkdir -p /opt/pinecall/wheels/$(WHEEL)'
	scp -q dist/pinecall-*.whl $(BOX):/opt/pinecall/wheels/$(WHEEL)/
	$(MAKE) release WHEEL=$(WHEEL)
	PINECALL_URL=https://$(firstword $(subst $(comma), ,$(DOMAINS))) uv run pytest -q tests/live
	$(MAKE) logs

rollback:         ## an older wheel still on the box: make rollback WHEEL=<sha>
	$(MAKE) release WHEEL=$(WHEEL)

release:
	ssh $(BOX) 'WHEEL=$(WHEEL) bash -s' < infra/box/release.sh

logs:             ## the journal of the runtime's units since the gateway last started, whole
	ssh $(BOX) 'journalctl -u pinecall-gateway -u "pinecall-worker@*" -u "pinecall-overflow@*" -u pinecall-migrate --no-pager -o cat --since "$$(systemctl show -p ActiveEnterTimestamp --value pinecall-gateway)"'

ssh:
	ssh $(BOX)

test-box:         ## every suite on the box's database through an ssh tunnel; the DSN is never printed
	@ssh -f -N -o ExitOnForwardFailure=yes -L $(TUNNEL):127.0.0.1:5432 $(BOX)
	@DATABASE_URL="$$(ssh $(BOX) sudo -n systemd-creds decrypt --name=DATABASE_URL /etc/credstore.encrypted/DATABASE_URL - \
	    | sed 's/127.0.0.1:5432/127.0.0.1:$(TUNNEL)/')" uv run pytest -q $(T); status=$$?; \
	  pkill -f "ssh -f -N -o ExitOnForwardFailure=yes -L $(TUNNEL):" ; exit $$status

comma := ,

.PHONY: check test db hooks box deploy rollback release logs ssh test-box
