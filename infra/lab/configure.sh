#!/usr/bin/env bash
# The lab's machines configured, one verb a step, run on the machine by measure.py over ssh:
#   generator <gen>          the vendors faked, the caller, the tenant agent, an S3 bucket
#   box <gen>                providers at the fakes, the lab's org and its keys, the fence
#   store <gen>              the box's store at the generator's bucket; the access key id on stdin
#   agent-env <box>          the agent's .env; the box's sandbox server key on stdin
#   agent                    the agent under `pinecall start`, until it connects
#   number <gen> <number>    the number routed to the agent, from the generator alone
# A secret comes on stdin, from the other machine, and is never an argument or a line printed.
set -euo pipefail

FAKES_PORT=8700
S3_PORT=9000
BUCKET=lab-recordings
AGENT_FRAMEWORK=0.9.13
NODE=v24.9.0

generator() {
    local gen=$1
    sudo useradd -m -s /bin/bash lab 2>/dev/null || true
    sudo chmod 755 /home/lab
    sudo tar xzf /tmp/lab.tgz -C /home/lab && sudo chown -R lab:lab /home/lab
    sudo systemctl stop fake-vendors lab-agent 2>/dev/null || true
    sudo systemctl reset-failed fake-vendors lab-agent 2>/dev/null || true
    sudo podman rm -f s3 >/dev/null 2>&1 || true
    cd /home/lab
    sudo -u lab /opt/pinecall/bin/uv run -q --with numpy python caller.py
    sudo systemd-run --unit=fake-vendors --uid=lab --working-directory=/home/lab \
        -p LimitNOFILE=1048576 /opt/pinecall/bin/uv run --with aiohttp --with numpy \
        python fake_vendors.py "$FAKES_PORT" >/dev/null
    curl -fsSL "https://nodejs.org/dist/$NODE/node-$NODE-linux-x64.tar.xz" | sudo tar xJ -C /opt
    sudo ln -sf "/opt/node-$NODE-linux-x64/bin/node" "/opt/node-$NODE-linux-x64/bin/npm" /usr/local/bin/
    cd /home/lab/agent
    printf '{"name":"lab","private":true,"type":"module","dependencies":{"pinecall":"%s"}}' \
        "$AGENT_FRAMEWORK" | sudo -u lab tee package.json >/dev/null
    sudo -u lab npm install --silent >/dev/null 2>&1
    # The bucket: SeaweedFS speaking S3, its pair drawn here and never printed.
    sudo sh -c 'umask 077; openssl rand -hex 10 > /home/lab/s3.user; openssl rand -hex 24 > /home/lab/s3.secret'
    local user secret
    user=$(sudo cat /home/lab/s3.user)
    secret=$(sudo cat /home/lab/s3.secret)
    printf '{"identities": [{"name": "lab", "credentials": [{"accessKey": "%s", "secretKey": "%s"}],
  "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}\n' "$user" "$secret" |
        sudo tee /home/lab/s3.json >/dev/null
    # SeaweedFS reads it as its own user, not root: readable, on a machine the lab destroys.
    sudo chmod 0644 /home/lab/s3.json
    sudo podman run -d --name s3 -p "$gen:$S3_PORT:8333" -v /home/lab/s3.json:/etc/s3.json:ro \
        -v s3data:/data docker.io/chrislusf/seaweedfs server -s3 -s3.config=/etc/s3.json -dir=/data >/dev/null
    for _ in $(seq 60); do curl -s -o /dev/null "$gen:$S3_PORT" && break; sleep 2; done
    RCLONE_CONFIG_M_TYPE=s3 RCLONE_CONFIG_M_PROVIDER=SeaweedFS RCLONE_CONFIG_M_ENDPOINT="http://$gen:$S3_PORT" \
        RCLONE_CONFIG_M_ACCESS_KEY_ID="$user" RCLONE_CONFIG_M_SECRET_ACCESS_KEY="$secret" rclone mkdir "m:$BUCKET"
    for _ in $(seq 30); do curl -s -o /dev/null "127.0.0.1:$FAKES_PORT" && break; sleep 2; done
    echo "generator ready"
}

box() {
    local gen=$1 org vendor
    local runtime="sudo pinecall-runtime"
    # The providers row replaced whole, as the console does: `providers seed` writes a first row only.
    sudo sh -c 'umask 077; printf "Authorization: Bearer %s\n" "$(systemd-creds decrypt --name=PINECALL_OPS_KEY /etc/credstore.encrypted/PINECALL_OPS_KEY -)" > /root/ops.header'
    sudo curl -fsS -o /dev/null -X PUT -H @/root/ops.header -H 'content-type: application/json' \
        --data-binary @/tmp/providers.json http://127.0.0.1:8080/v1/ops/providers
    $runtime orgs add lab --name lab >/dev/null 2>&1 || true
    org=$($runtime orgs list 2>/dev/null | awk '$2=="lab"{print $1}')
    $runtime orgs quota "$org" --env sandbox --concurrent-calls 1000000 --minutes 100000000 \
        --messages 100000000 --llm-tokens 100000000000 >/dev/null 2>&1
    sudo podman exec pinecall-postgres psql -q -U pinecall -d pinecall \
        -c "UPDATE orgs SET judging = false WHERE id = '$org'"
    for vendor in anthropic deepgram cartesia; do
        echo not-a-key-the-lab-fakes-every-vendor | $runtime orgs provider-key set "$org" "$vendor" >/dev/null 2>&1
    done
    sudo sh -c "umask 077; $runtime keys issue --org $org --env sandbox --label lab-agent --name lab-agent 2>/dev/null > /root/lab-agent.key"
    # The generator is the lab's carrier and the agent's server: SIP from it, the gateways and LiveKit to it.
    $runtime cell allow-worker "$gen" >/dev/null
    echo "add element inet pinecall carrier_signalling { $gen }" | sudo tee /etc/pinecall/nftables.d/lab.nft >/dev/null
    sudo nft -f /etc/nftables.conf
    echo "box ready, org $org"
}

store() {
    local gen=$1 id port
    id=$(cat)
    printf 'PINECALL_S3_ENDPOINT=http://%s:%s\nPINECALL_S3_REGION=us-east-1\nPINECALL_S3_ACCESS_KEY_ID=%s\nPINECALL_RECORDINGS_BUCKET=%s\n' \
        "$gen" "$S3_PORT" "$id" "$BUCKET" | sudo sh -c 'umask 077; cat > /etc/pinecall/store.env'
    sudo systemctl restart pinecall-gateway@8080 pinecall-gateway@8081
    for port in 8080 8081; do
        for _ in $(seq 60); do curl -s -o /dev/null -f "http://127.0.0.1:$port/v1/docs" && break; sleep 2; done
    done
}

# The key reaches the file by the pipe alone: never an argument a process list would show.
agent_env() {
    local box=$1
    { printf 'PINECALL_KEY='; grep -o -m1 'pc_test_[A-Za-z0-9_.-]*'; echo "PINECALL_URL=http://$box:8088"; } |
        sudo sh -c 'umask 077; cat > /home/lab/agent/.env; chown lab:lab /home/lab/agent/.env'
}

agent() {
    sudo systemd-run --unit=lab-agent --uid=lab --working-directory=/home/lab/agent \
        -p Restart=on-failure -p RestartSec=3 \
        -p EnvironmentFile=/home/lab/agent/.env --setenv=PATH=/usr/local/bin:/usr/bin:/bin \
        /home/lab/agent/node_modules/.bin/pinecall start >/dev/null
    for _ in $(seq 60); do sudo journalctl -u lab-agent -o cat | grep -q connected && break; sleep 2; done
    sudo journalctl -u lab-agent -o cat | grep -q connected || { echo "the agent did not connect"; exit 1; }
    echo "agent connected"
}

number() {
    local gen=$1 number=$2
    printf '{"number": "%s", "agent": "clinica-norte", "hooked": true, "networks": ["%s/32"]}\n' \
        "$number" "$gen" > /tmp/number.json
    sudo sh -c 'set -a; . /home/lab/agent/.env; set +a; curl -s -o /dev/null -w "number %{http_code}\n" \
        -X POST -H "Authorization: Bearer $PINECALL_KEY" -H "content-type: application/json" \
        "$PINECALL_URL/v1/numbers" -d @/tmp/number.json'
}

verb=${1:-}
shift || true
case "$verb" in
    generator | box | store | agent | number) "$verb" "$@" ;;
    agent-env) agent_env "$@" ;;
    *) echo "configure.sh generator|box|store|agent-env|agent|number …" >&2; exit 2 ;;
esac
