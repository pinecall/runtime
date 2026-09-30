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
UV=/opt/pinecall/bin/uv
[ -x /opt/pinecall/venv/bin/python ] || "$UV" venv --python /usr/bin/python3.12 /opt/pinecall/venv
"$UV" pip install --quiet --python /opt/pinecall/venv/bin/python --reinstall-package pinecall "$installed"
systemctl restart pinecall-migrate
systemctl restart pinecall-gateway
for _ in $(seq 60); do curl -fs -o /dev/null http://127.0.0.1:8080/ && break; sleep 1; done
curl -fsS -o /dev/null http://127.0.0.1:8080/ || { echo "the gateway did not answer in 60 s" >&2; exit 1; }
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
