# check · test · db · hooks · box · deploy · rollback · logs · ssh · test-box · tf-*.

# The new box's ssh alias; the old box (v1) is never a target of this file.
BOX      ?= example-box
DOMAINS  ?= box.pinecall.io,sandbox.pinecall.io
TUNNEL   ?= 15432
WHEEL    ?= $(shell git rev-parse --short HEAD)$(shell git diff --quiet HEAD || echo -dirty)

# The laptop's Postgres and Redis, for the suites only: in colima, on tmpfs, thrown away with
# their containers. The Redis is the box's image, with nothing kept.
DB_IMAGE  = pinecall/postgres:17-pgvector0.8.6-pgtextsearch1.4.0
DB_NAME   = pinecall-test-postgres
DB_PORT  ?= 55432
LOCAL_DSN = postgresql://pinecall:pinecall@127.0.0.1:$(DB_PORT)/pinecall
REDIS_IMAGE = docker.io/library/redis:8.10.2-alpine
REDIS_NAME  = pinecall-test-redis
REDIS_PORT ?= 56379
LOCAL_REDIS = redis://127.0.0.1:$(REDIS_PORT)/1
T        ?= tests

# Terraform's root modules (infra/terraform). Google's credentials are the gcloud login's, handed
# over as a short-lived token in the environment, never printed; AWS's are ~/.aws.
ENV      ?= production
TF        = terraform -chdir=infra/terraform/environments/$(ENV)
TF_AUTH   = GOOGLE_OAUTH_ACCESS_TOKEN="$$(gcloud auth print-access-token)"
TF_ROOTS  = infra/terraform/bootstrap infra/terraform/environments/production infra/terraform/environments/lab

check:            ## the rules and every suite that needs no database, on every core; terraform's form
	uv run pytest -q -n auto
	@if command -v terraform >/dev/null; then $(MAKE) --no-print-directory tf-check; fi

# Its own data directory: a root module initialized against its bucket would be reached again.
tf-check:         ## every root module formatted and valid, with no backend reached
	terraform fmt -check -recursive infra/terraform
	@for root in $(TF_ROOTS); do \
	  data=$(CURDIR)/.terraform-check/$$(basename $$root); \
	  TF_DATA_DIR=$$data terraform -chdir=$$root init -backend=false -input=false >/dev/null && \
	  TF_DATA_DIR=$$data terraform -chdir=$$root validate -no-color >/dev/null || exit 1; \
	done

tf-init:          ## ENV=production|lab: the root module's providers and its state in the bucket
	$(TF_AUTH) $(TF) init -input=false

tf-plan:          ## ENV=…: what an apply would change; "No changes." is the cloud as the repo says
	$(TF_AUTH) $(TF) plan -input=false

tf-apply:         ## ENV=…: the change made, after the plan is read and `yes` typed
	$(TF_AUTH) $(TF) apply -input=false

# The runtime whole on this laptop (infra/local): the box's services in docker, the gateway and a
# worker from the checkout, on the settings `make local` wrote to .local/env.
LOCAL_RUN = set -a; . ./.local/env; set +a; uv run pinecall-runtime
local:            ## Postgres, Redis and LiveKit in docker, the schema migrated, .local/env written once
	infra/local/up.sh

local-gateway:    ## the gateway from the checkout on 127.0.0.1:8080, against `make local`
	$(LOCAL_RUN) gateway

local-worker:     ## a worker of the sandbox fleet from the checkout, against `make local`
	$(LOCAL_RUN) worker start

local-down:       ## the compose stopped; its Postgres volume and .local/env kept
	docker compose -f infra/local/compose.yaml down

# The fleet's worker image (infra/packer): the wheel of this checkout, the box's settings for the
# world (no secret in them), the seats of the machine type the fleet runs as.
PROJECT  ?= example-project
WORLD    ?= production
SEATS    ?= 32
image:            ## WORLD=…: the fleet's worker image built by Packer, into pinecall-worker-<world>
	rm -rf dist && uv build --wheel --quiet
	ssh $(BOX) 'sudo pinecall-runtime cell worker-settings $(WORLD)' > .image-settings.tar
	$(TF_AUTH) packer build -var project=$(PROJECT) -var world=$(WORLD) -var seats=$(SEATS) \
	  -var box_address="$$($(TF_AUTH) terraform -chdir=infra/terraform/environments/production output -raw box_internal_address)" \
	  -var package="$$(ls $(CURDIR)/dist/pinecall-*.whl)" -var settings=$(CURDIR)/.image-settings.tar infra/packer; \
	  status=$$?; rm -f .image-settings.tar; exit $$status

test: db          ## every suite (or T=tests/log), on the local Postgres and Redis, on every core
	DATABASE_URL=$(LOCAL_DSN) PINECALL_REDIS_URL=$(LOCAL_REDIS) uv run pytest -q -n auto $(T)

# Durability is off: a test database that loses its last second on a crash loses nothing.
db:               ## the local Postgres and Redis: colima up, the image built once, both running
	@colima status >/dev/null 2>&1 || colima start
	@docker image inspect $(DB_IMAGE) >/dev/null 2>&1 || docker build -t $(DB_IMAGE) infra/postgres
	@docker inspect -f '{{.State.Running}}' $(DB_NAME) 2>/dev/null | grep -q true || { \
	  docker rm -f $(DB_NAME) >/dev/null 2>&1; \
	  docker run -d --name $(DB_NAME) -p 127.0.0.1:$(DB_PORT):5432 --tmpfs /var/lib/postgresql/data \
	    -e POSTGRES_USER=pinecall -e POSTGRES_PASSWORD=pinecall -e POSTGRES_DB=pinecall $(DB_IMAGE) \
	    -c fsync=off -c synchronous_commit=off -c full_page_writes=off >/dev/null; }
	@until docker exec $(DB_NAME) pg_isready -U pinecall -d pinecall >/dev/null 2>&1; do sleep 1; done
	@docker inspect -f '{{.State.Running}}' $(REDIS_NAME) 2>/dev/null | grep -q true || { \
	  docker rm -f $(REDIS_NAME) >/dev/null 2>&1; \
	  docker run -d --name $(REDIS_NAME) -p 127.0.0.1:$(REDIS_PORT):6379 --tmpfs /data $(REDIS_IMAGE) \
	    redis-server --save '' --appendonly no >/dev/null; }
	@until docker exec $(REDIS_NAME) redis-cli ping >/dev/null 2>&1; do sleep 1; done

hooks:            ## the pre-commit hook: `make check`
	git config core.hooksPath .githooks

box:              ## infra/ to the box and install.sh run there: once, and after infra/box changes
	rsync -a --delete --delete-excluded --exclude .terraform/ --exclude '*.tfstate*' infra/ $(BOX):/tmp/pinecall-infra/
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
	ssh $(BOX) 'journalctl -u "pinecall-gateway@*" -u "pinecall-worker*@*" -u "pinecall-overflow@*" -u pinecall-migrate --no-pager -o cat --since "$$(systemctl show -p ActiveEnterTimestamp --value pinecall-gateway@8080)"'

ssh:
	ssh $(BOX)

test-box:         ## every suite on the box's database through an ssh tunnel; the DSN is never printed
	@ssh -f -N -o ExitOnForwardFailure=yes -L $(TUNNEL):127.0.0.1:5432 $(BOX)
	@DATABASE_URL="$$(ssh $(BOX) sudo -n systemd-creds decrypt --name=DATABASE_URL /etc/credstore.encrypted/DATABASE_URL - \
	    | sed 's/127.0.0.1:5432/127.0.0.1:$(TUNNEL)/')" uv run pytest -q $(T); status=$$?; \
	  pkill -f "ssh -f -N -o ExitOnForwardFailure=yes -L $(TUNNEL):" ; exit $$status

comma := ,

.PHONY: check test db hooks box deploy rollback release logs ssh test-box tf-check tf-init tf-plan tf-apply image local local-gateway local-worker local-down
