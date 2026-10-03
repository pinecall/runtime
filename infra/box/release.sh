#!/usr/bin/env bash
# One release on the box. Two ways to name what is released:
#   WHEEL=<sha>        /opt/pinecall/wheels/<sha>/, as the deploy account: `make deploy` and
#                      `make rollback` over ssh; the restarts go through the polkit rule.
#   PACKAGE=<spec>     anything uv installs — `pinecall==0.1.2`, a wheel's path — as root:
#                      `pinecall-runtime box up`, from the package itself.
set -euo pipefail
if [ -n "${WHEEL:-}" ]; then
    installed="$(ls /opt/pinecall/wheels/"$WHEEL"/pinecall-*.whl)[voice]"
else
    case "$PACKAGE" in
        *.whl) installed="$PACKAGE[voice]" ;;
        pinecall==*) installed="pinecall[voice]==${PACKAGE#pinecall==}" ;;
        *) echo "PACKAGE is pinecall==<version> or a wheel's path, not $PACKAGE" >&2; exit 2 ;;
    esac
fi
# A world's fleet loop runs this package too, and reads the gateway's answers strictly: it is
# stopped before the package changes and started again when the release ends, whichever way it
# ends. Stopped, it makes and lets go of nothing; the machines it sized keep their calls.
looping=()
for world in production sandbox; do
    if systemctl is-enabled --quiet "pinecall-fleet-loop@$world"; then
        looping+=("pinecall-fleet-loop@$world")
    fi
done
if [ ${#looping[@]} -gt 0 ]; then
    systemctl stop "${looping[@]}"
    trap 'systemctl start "${looping[@]}"' EXIT
fi
UV=/opt/pinecall/bin/uv
[ -x /opt/pinecall/venv/bin/python ] || "$UV" venv --python /usr/bin/python3.12 /opt/pinecall/venv
"$UV" pip install --quiet --python /opt/pinecall/venv/bin/python --reinstall-package pinecall "$installed"
systemctl restart pinecall-migrate
# Two gateways, replaced one at a time: Caddy sends everything to the other while one restarts.
answering() {
    for _ in $(seq 60); do curl -fs -o /dev/null "http://127.0.0.1:$1/" && return 0; sleep 1; done
    echo "the gateway on $1 did not answer in 60 s" >&2
    return 1
}
if systemctl is-enabled --quiet pinecall-gateway@8081; then
    systemctl restart pinecall-gateway@8081
    answering 8081
fi
# A box installed with one gateway: it goes once the second answers, and the first takes its port.
if systemctl is-active --quiet pinecall-gateway; then
    systemctl stop pinecall-gateway
fi
systemctl restart pinecall-gateway@8080
answering 8080
# Two workers per world, replaced one at a time: the second while the first takes the calls, then
# the first once the second is back. `restart` of a Type=notify unit returns when the new process is
# registered with LiveKit and heard by the gateway, after the old one drained (up to ten minutes).
systemctl restart pinecall-worker-b@production pinecall-worker-b@sandbox
# A box installed with one worker per world: that one drains now that the second takes the calls.
for world in production sandbox; do
    if systemctl is-active --quiet "pinecall-worker@$world"; then
        systemctl stop "pinecall-worker@$world"
    fi
done
systemctl restart pinecall-worker-a@production pinecall-worker-a@sandbox pinecall-overflow@production
systemctl start pinecall-doctor
echo "released ${WHEEL:-$PACKAGE}"
