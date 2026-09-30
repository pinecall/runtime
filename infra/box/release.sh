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
for _ in $(seq 60); do curl -fsS -o /dev/null http://127.0.0.1:8080/ && break; sleep 1; done
curl -fsS -o /dev/null http://127.0.0.1:8080/ || { echo "the gateway did not answer in 60 s" >&2; exit 1; }
systemctl restart pinecall-worker@production pinecall-worker@sandbox pinecall-overflow@production
systemctl start pinecall-doctor
echo "released ${WHEEL:-$PACKAGE}"
