#!/usr/bin/env bash
# The box, as every script here reaches it: where its parts live, how they get there, and the
# --dry-run they all honour. Sourced, never run: `. "$(dirname "$0")/remote.sh"`.

# Which machine, and where its parts live. Every path on the box is written once, here.
# BOX is an ssh destination: an alias in ~/.ssh/config, or user@address. The box carries the same
# name as a network tag (TAG, in firewall.sh), so the machine is one word everywhere.
BOX="${BOX:-pinecall-v2-box}"
APP_DIR=/opt/pinecall/app
MEDIA_DIR=/opt/pinecall/media
TOOLS_DIR=/opt/pinecall/bin
VENV_DIR=/opt/pinecall/venv
ENV_FILE=/etc/pinecall/pinecall.env
UV="$TOOLS_DIR/uv"

# The account the two units run as. It owns the virtualenv, reads the code, and holds no
# credential for anything outside this machine — the code arrives by rsync, from a laptop.
SERVICE_USER=pinecall

# Where a key an operator must keep is written on the box, under the login account's home and
# readable by nobody else. Never a deploy's terminal: that is a laptop, a CI log and a transcript.
OPERATOR_DIR=".pinecall"

# --dry-run prints what would run and runs none of it, which is how a script that opens ports and
# restarts a live phone line is read before it is trusted.
DRY_RUN=false

reading_the_flags() {
  for flag in "$@"; do
    case "$flag" in
      --dry-run) DRY_RUN=true ;;
      *)
        echo "unknown flag: $flag  (only --dry-run)" >&2
        exit 2
        ;;
    esac
  done
}

# Everything that changes the box goes through here, so --dry-run cannot miss one.
run() {
  if [ "$DRY_RUN" = true ]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

on_the_box() {
  run ssh "$BOX" "$1"
}

ship() {
  run scp -q -r "$@"
}

# scp arrives as the login account, and /opt/pinecall belongs to the service user: anything the
# service user must own goes to /tmp first and is moved with sudo. The unit files take the same
# path for the same reason — see install_the_unit.
ship_into() {
  local destination="$1"
  shift
  local staged=()
  local path
  for path in "$@"; do
    staged+=("/tmp/$(basename "$path")")
  done
  ship "$@" "$BOX:/tmp/"
  on_the_box "sudo cp -r ${staged[*]} $destination && sudo chown -R $SERVICE_USER:$SERVICE_USER $destination && sudo rm -rf ${staged[*]}"
}

say() {
  printf '\n── %s\n' "$1"
}

# Every runtime verb the box runs: as the service account, with the box's own environment loaded
# from the one file that holds it (never through argv, where a `ps` would read the keys), and with
# the exact extras both units ask for — a run that asks for less uninstalls the other process's
# half, the worker's vendors or the gateway's judges, and the phone goes quiet with a clean log.
# The same `--extra runtime` is in both unit files and in shipway.yml's postSync.
as_the_service() {
  echo "sudo -u $SERVICE_USER bash -c 'set -a; . $ENV_FILE; set +a; cd $APP_DIR && $UV run --frozen --project runtime --extra runtime $1'"
}

# A unit is furniture, not a deploy artifact: it is installed and enabled when the box is built,
# and STARTED by the deploy that first puts code under it. Enabling one on an empty /opt/pinecall/
# app would restart-loop a unit that has nothing to run, which reads as a broken box.
# The environment file beside it is the box's own, and nothing here ever writes that one.
install_the_unit() {
  local unit="$1" here="$2"
  say "the unit: $unit"
  ship "$here/$unit.service" "$BOX:/tmp/$unit.service"
  on_the_box "sudo mv /tmp/$unit.service /etc/systemd/system/$unit.service && sudo systemctl daemon-reload && sudo systemctl enable -q $unit"
}
