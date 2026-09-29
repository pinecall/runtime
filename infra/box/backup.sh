#!/usr/bin/env bash
# One backup of the box, run nightly by pinecall-backup.timer as root (podman's container is root's):
# the database as pg_dump's custom archive, and the recordings as a tar, each encrypted to the age
# public key in /etc/pinecall/backup.age.pub, whose private half never reaches the box. A manifest
# keeps each file's sha256 before encryption, so a restore can prove it decrypted the same bytes.
# Kept 7 days on the box; with PINECALL_BACKUP_BUCKET in /etc/pinecall/backup.env (the operator's,
# which install.sh never writes), copied to that bucket too with the VM's own identity; the bucket's
# lifecycle rule forgets them after 35 days.
set -euo pipefail

HERE=/var/lib/pinecall/backups
KEY=/etc/pinecall/backup.age.pub
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
KEEP_DAYS=7
[ -f /etc/pinecall/backup.env ] && . /etc/pinecall/backup.env
[ -s "$KEY" ] || { echo "no $KEY: nothing is encrypted to anybody, so nothing is written" >&2; exit 1; }
install -d -m 0700 "$HERE"
cd "$HERE"

# Written and read back inside the container, as a file: pg_restore reads the whole archive as the
# script it would run, so a dump nobody could restore fails here, not on the day it is needed.
# Nothing crosses podman's stdin, which drops bytes under a large write.
INSIDE="/tmp/$STAMP.db.dump"
podman exec pinecall-postgres pg_dump -U pinecall -d pinecall -Fc -f "$INSIDE"
podman exec pinecall-postgres pg_restore -f /dev/null "$INSIDE"
podman cp "pinecall-postgres:$INSIDE" "$STAMP.db.dump"
podman exec pinecall-postgres rm -f "$INSIDE"
tar -C /var/lib/pinecall -cf "$STAMP.recordings.tar" recordings

sha256sum "$STAMP.db.dump" "$STAMP.recordings.tar" > "$STAMP.sha256"
for plain in "$STAMP.db.dump" "$STAMP.recordings.tar"; do
    age -R "$KEY" -o "$plain.age" "$plain"
    rm -f "$plain"
done

find "$HERE" -maxdepth 1 -type f -mtime +"$KEEP_DAYS" -delete

if [ -n "${PINECALL_BACKUP_BUCKET:-}" ]; then
    gcloud storage cp --quiet "$STAMP".* "gs://$PINECALL_BACKUP_BUCKET/$STAMP/"
fi
ls -l "$HERE/$STAMP".* | awk '{print $5, $NF}'
