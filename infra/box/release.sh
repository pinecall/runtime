#!/usr/bin/env bash
# One release on the box, as the deploy account: WHEEL=<sha> names /opt/pinecall/wheels/<sha>/.
# Run by `make deploy` and `make rollback` over ssh; the restarts go through the polkit rule.
set -euo pipefail
wheel="$(ls /opt/pinecall/wheels/"$WHEEL"/pinecall-*.whl)"
UV=/opt/pinecall/bin/uv
[ -x /opt/pinecall/venv/bin/python ] || "$UV" venv --python /usr/bin/python3.12 /opt/pinecall/venv
"$UV" pip install --quiet --python /opt/pinecall/venv/bin/python --reinstall-package pinecall "$wheel[voice]"
systemctl restart pinecall-migrate
systemctl restart pinecall-gateway
for _ in $(seq 60); do curl -fsS -o /dev/null http://127.0.0.1:8080/ && break; sleep 1; done
curl -fsS -o /dev/null http://127.0.0.1:8080/ || { echo "the gateway did not answer in 60 s" >&2; exit 1; }
systemctl restart pinecall-worker@production pinecall-worker@sandbox pinecall-overflow@production
systemctl start pinecall-doctor
echo "released $WHEEL"
