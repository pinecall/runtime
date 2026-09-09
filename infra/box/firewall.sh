#!/usr/bin/env bash
# firewall.sh — the box's four ingress rules, and the one that matters: 5060 to the carrier only.
#
#   ./infra/box/firewall.sh --dry-run     # print the gcloud calls, make none
#   ./infra/box/firewall.sh               # create them, or update them in place
#
# A SIP port open to the internet is found and probed within hours, and every INVITE a scanner
# sends is a room, a job and a model's bill if it gets through. So signalling is allowed from the
# carrier's networks and DENIED from everywhere else, explicitly: the deny is written down so a
# reader can see it, and so a change to the allow rule cannot quietly leave the port open.
# The networks come from infra/box/carrier-signalling-cidrs.txt, which is the only place they live.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=infra/box/remote.sh
. "$HERE/remote.sh"

reading_the_flags "$@"

NETWORK="${NETWORK:-default}"

# These rules face the internet, and gcloud's active project is whatever the operator last worked
# on. PROJECT names one for this run; gcloud reads the variable exactly as it reads --project.
if [ -n "${PROJECT:-}" ]; then
  export CLOUDSDK_CORE_PROJECT="$PROJECT"
fi
# The rules bite the machines carrying this tag, so a second box joins the fleet by being tagged.
TAG="${TAG:-pinecall-v2-box}"

CARRIER="$(python3 "$HERE/../scripts/carrier_cidrs.py")"

# gcloud has no upsert: describe first, then update the rule or create it. Both paths end with the
# same rule, which is what makes running this twice a no-op.
a_rule() {
  local name="$1" action="$2" ports="$3" sources="$4" priority="$5"
  # A dry run asks the cloud nothing at all — not even this read, which can sit waiting on
  # credentials — so it prints the create and names what a standing rule would turn it into.
  if [ "$DRY_RUN" = false ] && gcloud compute firewall-rules describe "$name" \
      --format="value(name)" >/dev/null 2>&1; then
    run gcloud compute firewall-rules update "$name" \
      --rules="$ports" --source-ranges="$sources" --priority="$priority" --quiet
    return
  fi
  run gcloud compute firewall-rules create "$name" \
    --network="$NETWORK" --direction=INGRESS --action="$action" \
    --rules="$ports" --source-ranges="$sources" --priority="$priority" \
    --target-tags="$TAG" --quiet
  if [ "$DRY_RUN" = true ]; then
    printf '  (a rule of that name already standing is updated in place instead)\n'
  fi
}

say "sip signalling: the carrier, at priority 1000"
a_rule pinecall-sip-signalling ALLOW udp:5060,tcp:5060 "$CARRIER" 1000

say "sip signalling: everybody else, denied at 1100 — read this rule, it is the fence"
a_rule pinecall-sip-denied DENY udp:5060,tcp:5060 0.0.0.0/0 1100

# Media legitimately arrives from any of the carrier's media addresses, and from any browser
# anywhere, so it stays open. Without a signalling INVITE that was admitted, nothing is listening
# on these ports for a stranger's packet anyway.
say "media: rtp and webrtc, open"
a_rule pinecall-media ALLOW udp:10000-10199,udp:7882,tcp:7881 0.0.0.0/0 1000

say "the web: caddy, and nothing else"
a_rule pinecall-web ALLOW tcp:80,tcp:443 0.0.0.0/0 1000

# 22 is left exactly as the project already has it: an SSH rule this script does not own is an
# SSH rule this script cannot lock somebody out with.
say "read them back"
run gcloud compute firewall-rules list \
  --filter="name~^pinecall-" --format="table(name,priority,direction,allowed[],denied[],sourceRanges.list())"
