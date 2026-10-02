#!/usr/bin/env bash
# `make local`: the compose up and healthy, the schema migrated, and .local/env written once — the
# laptop's own secrets (a vault key, an ops key, the sandbox fleet's key) drawn here, 0600, never
# printed. Run again, it keeps the secrets and the database.
set -euo pipefail
cd "$(dirname "$0")/../.."
ENV=.local/env
docker compose -f infra/local/compose.yaml up -d --build --wait postgres redis livekit
mkdir -p .local/recordings
if [ ! -f "$ENV" ]; then
    umask 077
    {
        echo "DATABASE_URL=postgresql://pinecall:pinecall@127.0.0.1:55433/pinecall"
        echo "PINECALL_REDIS_URL=redis://127.0.0.1:56380/0"
        echo "LIVEKIT_URL=ws://127.0.0.1:7880"
        echo "LIVEKIT_API_KEY=devkey"
        echo "LIVEKIT_API_SECRET=a-laptop-only-livekit-secret-0123456789"
        echo "PINECALL_GATEWAY_URL=http://127.0.0.1:8080"
        echo "PINECALL_DOMAIN=localhost"
        echo "PINECALL_SANDBOX_DOMAIN=sandbox.localhost"
        echo "PINECALL_RECORDINGS=$PWD/.local/recordings"
        echo "PINECALL_FLEET=pinecall-sandbox"
        echo "PINECALL_MAX_JOBS=4"
        echo "PINECALL_WORKER_HTTP_PORT=8182"
        printf 'PINECALL_VAULT_KEY=%s\n' "$(openssl rand -base64 32 | tr '+/' '-_')"
        printf 'PINECALL_OPS_KEY=pc_ops_%s\n' "$(openssl rand -hex 24)"
    } > "$ENV"
fi
set -a
# shellcheck source=/dev/null
. "./$ENV"
set +a
uv run pinecall-runtime migrate up 2>&1 | tail -1
if ! grep -q '^PINECALL_WORKER_KEY=' "$ENV"; then
    umask 077
    key="$(uv run pinecall-runtime keys fleet sandbox 2>/dev/null)"
    printf 'PINECALL_WORKER_KEY=%s\n' "$key" >> "$ENV"
fi
echo "local: Postgres :55433, Redis :56380, LiveKit :7880 up; $ENV holds the runtime's settings"
echo "  make local-gateway   the gateway on 127.0.0.1:8080, both worlds"
echo "  make local-worker    a worker of the sandbox fleet"
