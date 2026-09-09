#!/usr/bin/env bash
# first_run.sh — the three things a box needs ONCE, after its first `shipway deploy`: the schema,
# the default org's key, and the org's key the worker knocks with. Every later deploy is shipway
# alone, and this script run twice does nothing.
#
#   ./infra/box/first_run.sh --dry-run      # print every step, change nothing
#   ./infra/box/first_run.sh
#
# It puts no code on the box and writes no environment line the box did not make itself: the code
# arrives by rsync from a laptop (shipway.yml at the root of this tree), and the secrets were
# generated on the box by setup.sh. Nothing on this machine can reach the repository.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=infra/box/remote.sh
. "$HERE/remote.sh"

reading_the_flags "$@"

# On a FRESH database `migrate up` also creates the `default` org and issues its key — the only
# moment that key exists in the clear. It is written on the box, into a file only the login
# account can read, and never onto this terminal: a terminal is a laptop, a CI log and a
# transcript at once. A key is stored as its sha256; no verb anywhere reads one back.
say "the schema, and the default org's key on the run that creates it"
on_the_box "$(
  cat <<REMOTE
umask 077 && mkdir -p \$HOME/$OPERATOR_DIR
out=\$HOME/$OPERATOR_DIR/migrate.out
status=0
$(as_the_service 'pinecall-runtime migrate up') > \$out 2>&1 || status=\$?
grep -v '^pk_' \$out | sed 's/^/   /'
if grep -q '^pk_' \$out; then
  grep '^pk_' \$out > \$HOME/$OPERATOR_DIR/box-org-key
  echo "   the default org's key was issued: \$HOME/$OPERATOR_DIR/box-org-key, and nowhere else"
fi
rm -f \$out
exit \$status
REMOTE
)"

# The worker knocks at the gateway with a FLEET's key, never with the operator's and never with a
# development key: PINECALL_DEV_KEY makes the gateway open no database at all, which on a box means
# no routes table. docs/decisions/keys.md says why the three are different things.
say "the org's own key, issued once and never rewritten"
on_the_box "$(
  cat <<REMOTE
# sudo, because the file is 0600 and owned by the service user: a plain grep is REFUSED it
# rather than told the answer, and would issue this box a second key on every run.
if sudo grep -q '^PINECALL_API_KEY=' $ENV_FILE; then
  echo "   $ENV_FILE already carries PINECALL_API_KEY, and this script never rewrites one"
else
  issued=\$($(as_the_service "pinecall-runtime keys issue --org default --label the-worker-on-this-box") | head -1)
  echo "PINECALL_API_KEY=\$issued" | sudo tee -a $ENV_FILE >/dev/null
  echo "   issued the org's key; it was printed once, on the box, and is in $ENV_FILE"
fi
REMOTE
)"

# The worker read its environment when it started, so a key appended after that is a key it has
# never seen. A worker that started is also not a worker that REGISTERED: until the SFU has it in
# the fleet a call rings into a room nobody joins, and the line it prints is the only proof.
say "and the units, with the key they now have"
on_the_box "sudo systemctl restart pinecall-gateway pinecall-worker"
run sleep 5
on_the_box "systemctl is-active pinecall-gateway pinecall-worker && journalctl -u pinecall-worker -n 20 --no-pager -o cat | grep -i 'registered' || echo '   no registration line yet: journalctl -u pinecall-worker -f'"

say "the whole path, without a phone"
echo "   cd runtime && uv run python ../infra/scripts/sip_probe.py --host <address> --domain <name> --dialled <number>"
