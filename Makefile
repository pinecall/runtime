# check · test · db · test-sandbox · hooks · ssh · logs.

BOX      ?= pinecall-v2-box
INSTANCE ?= sandbox
TUNNEL   ?= 15432
CREDSTORE = /etc/pinecall/instances/$(INSTANCE).credstore

# The laptop's Postgres, for the suites only: in colima, on tmpfs, thrown away with the container.
DB_IMAGE  = pinecall/postgres:17-pgvector0.8.6-pgtextsearch1.4.0
DB_NAME   = pinecall-test-postgres
DB_PORT  ?= 55432
LOCAL_DSN = postgresql://pinecall:pinecall@127.0.0.1:$(DB_PORT)/pinecall
T        ?= tests

check:            ## the rules and every suite that needs no database
	uv run pytest -q

test: db          ## every suite (or T=tests/log), on the local Postgres
	DATABASE_URL=$(LOCAL_DSN) uv run pytest -q $(T)

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

test-sandbox:     ## every suite, on the sandbox database through an SSH tunnel; the DSN is never printed
	@ssh -f -N -o ExitOnForwardFailure=yes -L $(TUNNEL):127.0.0.1:5432 $(BOX)
	@DATABASE_URL="$$(ssh $(BOX) sudo -n systemd-creds decrypt --name=DATABASE_URL $(CREDSTORE)/DATABASE_URL - \
	    | sed 's/127.0.0.1:5432/127.0.0.1:$(TUNNEL)/')" uv run pytest -q $(T); status=$$?; \
	  pkill -f "ssh -f -N -o ExitOnForwardFailure=yes -L $(TUNNEL):" ; exit $$status

hooks:            ## the pre-commit hook: `make check`
	git config core.hooksPath .githooks

ssh:
	ssh $(BOX)

logs:             ## the journal of both units of INSTANCE since their last start, whole
	ssh $(BOX) 'journalctl -u pinecall-gateway@$(INSTANCE) -u pinecall-worker@$(INSTANCE) --no-pager -o cat _SYSTEMD_INVOCATION_ID=$$(systemctl show -p InvocationID --value pinecall-gateway@$(INSTANCE))'

.PHONY: check test db test-sandbox hooks ssh logs
