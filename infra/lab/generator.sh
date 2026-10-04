#!/usr/bin/env bash
# The lab's generator configured, one verb a step, run on it by measure.py over gcloud ssh:
#   setup                    the vendors faked (port 8700), the caller's audio, the agent installed
#   agent <url> <world>      the tenant's agent in that world (`pinecall start`, `--prod` for
#                            production), until it connects; the world's key on stdin
#   number <public> <number> <world>
#                            the number routed to the world's agent, hooked from the generator's
#                            public address alone (a carrier reaches the cluster from the internet)
#   store                    an S3 the recordings go to (moto, on 9000), its bucket made
#   stored                   the keys the bucket holds, one a line
# A secret comes on stdin and is never an argument or a line printed.
set -euo pipefail

FAKES_PORT=8700
STORE_PORT=9000
BUCKET=lab-recordings
AGENT_FRAMEWORK=0.9.13

setup() {
    sudo useradd -m -s /bin/bash lab 2>/dev/null || true
    sudo chmod 755 /home/lab
    sudo tar xzf /tmp/lab.tgz -C /home/lab && sudo chown -R lab:lab /home/lab
    # A world's agent is its own verb's to stop: setting the other world up leaves this one running.
    sudo systemctl stop fake-vendors 2>/dev/null || true
    sudo systemctl reset-failed fake-vendors 2>/dev/null || true
    cd /home/lab
    sudo -u lab uv run -q --with numpy python caller.py
    sudo systemd-run --unit=fake-vendors --uid=lab --working-directory=/home/lab \
        -p LimitNOFILE=1048576 --setenv=PATH=/usr/local/bin:/usr/bin:/bin \
        uv run --with aiohttp --with numpy python fake_vendors.py "$FAKES_PORT" >/dev/null
    cd /home/lab/agent
    printf '{"name":"lab","private":true,"type":"module","dependencies":{"pinecall":"%s"}}' \
        "$AGENT_FRAMEWORK" | sudo -u lab tee package.json >/dev/null
    sudo -u lab npm install --silent >/dev/null 2>&1
    for _ in $(seq 30); do curl -s -o /dev/null "127.0.0.1:$FAKES_PORT" && break; sleep 2; done
    echo "generator ready"
}

# The key reaches the world's file by the pipe alone; one agent a world, each its own unit.
agent() {
    local url=$1 world=$2 prod=""
    [ "$world" = production ] && prod=--prod
    { printf 'PINECALL_KEY='; grep -o -m1 'pc_\(live\|test\)_[A-Za-z0-9_.-]*'; echo "PINECALL_URL=$url"; } |
        sudo sh -c "umask 077; cat > /home/lab/agent/$world.env; chown lab:lab /home/lab/agent/$world.env"
    sudo systemctl stop "lab-agent-$world" 2>/dev/null || true
    sudo systemctl reset-failed "lab-agent-$world" 2>/dev/null || true
    sudo systemd-run --unit="lab-agent-$world" --uid=lab --working-directory=/home/lab/agent \
        -p Restart=on-failure -p RestartSec=3 \
        -p EnvironmentFile="/home/lab/agent/$world.env" --setenv=PATH=/usr/local/bin:/usr/bin:/bin \
        /home/lab/agent/node_modules/.bin/pinecall start $prod >/dev/null
    for _ in $(seq 60); do sudo journalctl -u "lab-agent-$world" -o cat | grep -q connected && break; sleep 2; done
    sudo journalctl -u "lab-agent-$world" -o cat | grep -q connected || {
        sudo journalctl -u "lab-agent-$world" -o cat | tail -20
        echo "the $world agent did not connect"
        exit 1
    }
    echo "the $world agent connected"
}

number() {
    local public=$1 number=$2 world=$3 status
    printf '{"number": "%s", "agent": "clinica-norte", "hooked": true, "networks": ["%s/32"]}\n' \
        "$number" "$public" > /tmp/number.json
    status=$(sudo env WORLD="$world" sh -c 'set -a; . "/home/lab/agent/$WORLD.env"; set +a; curl -s -o /tmp/number.out -w "%{http_code}" \
        -X POST -H "Authorization: Bearer $PINECALL_KEY" -H "pinecall-env: $WORLD" \
        -H "content-type: application/json" "$PINECALL_URL/v1/numbers" -d @/tmp/number.json')
    case "$status" in
        2*) echo "number $status" ;;
        409) echo "number routed already" ;;
        *) echo "number $status: $(cat /tmp/number.out)"; exit 1 ;;
    esac
}

# moto answers S3 on its own wire and takes any key: the lab's store proves what the runtime sends.
store() {
    sudo systemctl stop lab-store 2>/dev/null || true
    sudo systemctl reset-failed lab-store 2>/dev/null || true
    sudo systemd-run --unit=lab-store --uid=lab --working-directory=/home/lab \
        --setenv=PATH=/usr/local/bin:/usr/bin:/bin \
        uv run --with "moto[server]" moto_server -H 0.0.0.0 -p "$STORE_PORT" >/dev/null
    for _ in $(seq 60); do curl -s -o /dev/null "127.0.0.1:$STORE_PORT" && break; sleep 2; done
    curl -fsS -o /dev/null -X PUT "127.0.0.1:$STORE_PORT/$BUCKET"
    echo "store ready, bucket $BUCKET"
}

stored() {
    curl -fsS "127.0.0.1:$STORE_PORT/$BUCKET" | grep -o '<Key>[^<]*</Key>' | sed 's/<[^>]*>//g'
}

verb=${1:-}
shift || true
case "$verb" in
    setup | agent | number | store | stored) "$verb" "$@" ;;
    *) echo "generator.sh setup|agent <url> <world>|number <public> <number> <world>|store|stored" >&2; exit 2 ;;
esac
