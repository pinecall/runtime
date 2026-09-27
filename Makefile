# check · test · hooks · ssh · logs.

BOX      ?= pinecall-v2-box
INSTANCE ?= sandbox
TUNNEL   ?= 15432
CREDSTORE = /etc/pinecall/instances/$(INSTANCE).credstore

check:            ## the rules and every suite that needs no database
	uv run pytest -q

test:             ## every suite, on the sandbox database through an SSH tunnel; the DSN is never printed
	@ssh -f -N -o ExitOnForwardFailure=yes -L $(TUNNEL):127.0.0.1:5432 $(BOX)
	@DATABASE_URL="$$(ssh $(BOX) sudo -n systemd-creds decrypt --name=DATABASE_URL $(CREDSTORE)/DATABASE_URL - \
	    | sed 's/127.0.0.1:5432/127.0.0.1:$(TUNNEL)/')" uv run pytest -q; status=$$?; \
	  pkill -f "ssh -f -N -o ExitOnForwardFailure=yes -L $(TUNNEL):" ; exit $$status

hooks:            ## the pre-commit hook: `make check`
	git config core.hooksPath .githooks

ssh:
	ssh $(BOX)

logs:             ## the journal of both units of INSTANCE since their last start, whole
	ssh $(BOX) 'journalctl -u pinecall-gateway@$(INSTANCE) -u pinecall-worker@$(INSTANCE) --no-pager -o cat _SYSTEMD_INVOCATION_ID=$$(systemctl show -p InvocationID --value pinecall-gateway@$(INSTANCE))'

.PHONY: check test hooks ssh logs
