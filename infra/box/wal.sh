#!/usr/bin/env bash
# The database's write-ahead log archived off the box, for a restore to any minute, as root:
#   wal.sh apply    archiving on when /etc/pinecall/backup.env names PINECALL_BACKUP_BUCKET and the
#                   backup key exists, off (as a box without a bucket has always been) otherwise;
#                   Postgres restarts only when that changes. install.sh runs it; so does the
#                   operator after editing backup.env
#   wal.sh ship     every segment Postgres spooled, compressed, encrypted to the backup key and
#                   copied to gs://<bucket>/wal/, then removed; pinecall-wal.timer, every 10 s
#   wal.sh fetch <stamp> <age private key> <target time>   what a restore replays: that night's
#                   base backup and every segment since it, decrypted into the spool's restore/, and
#                   the recovery settings that stop at the target (docs/a-box-in-production.md)
# Postgres never waits on the network: its archive_command copies a finished segment to this disk
# and returns. When the bucket is unreachable the spool grows by what the box writes, and the
# doctor says so after five minutes; a disk that fills stops Postgres, as a full pg_wal would.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "wal.sh runs as root" >&2; exit 2; }
# Segment names compare as plain bytes: 24 hex digits, the timeline first.
export LC_ALL=C
SPOOL=/var/lib/pinecall/wal
KEY=/etc/pinecall/backup.age.pub
[ -f /etc/pinecall/backup.env ] && . /etc/pinecall/backup.env
BUCKET="${PINECALL_BACKUP_BUCKET:-}"
# Run by Postgres inside its container, where the spool is mounted at the same path. A segment
# lands whole or not at all (.part, then a rename), synced before Postgres is told it is safe; one
# already there fails the retry until the shipper has taken it.
ARCHIVE="test ! -f $SPOOL/%f && cp %p $SPOOL/%f.part && sync $SPOOL/%f.part && mv $SPOOL/%f.part $SPOOL/%f"

sql() {
    podman exec pinecall-postgres psql -U pinecall -d pinecall -Atqc "$1"
}

case "${1:-}" in
apply)
    wanted=off
    [ -n "$BUCKET" ] && [ -s "$KEY" ] && wanted=on
    for _ in $(seq 60); do podman healthcheck run pinecall-postgres >/dev/null 2>&1 && break; sleep 1; done
    if [ "$wanted" = on ]; then
        sql "ALTER SYSTEM SET archive_mode = on"
        # The RPO: a quiet minute still closes its segment and ships it.
        sql "ALTER SYSTEM SET archive_timeout = '60s'"
        sql "ALTER SYSTEM SET archive_command = '$ARCHIVE'"
        systemctl enable --now pinecall-wal.timer
    else
        sql "ALTER SYSTEM RESET archive_mode"
        sql "ALTER SYSTEM RESET archive_timeout"
        sql "ALTER SYSTEM RESET archive_command"
        systemctl disable --now pinecall-wal.timer 2>/dev/null || true
    fi
    sql "SELECT pg_reload_conf()" >/dev/null
    # archive_mode is read at start alone: a change restarts Postgres, a few seconds, once.
    if [ "$(sql "SELECT pending_restart FROM pg_settings WHERE name = 'archive_mode'")" = t ]; then
        systemctl restart pinecall-postgres
    fi
    echo "wal archive $wanted"
    ;;
ship)
    [ -n "$BUCKET" ] || { echo "no PINECALL_BACKUP_BUCKET in /etc/pinecall/backup.env: the spool keeps its segments" >&2; exit 1; }
    cd "$SPOOL"
    find . -maxdepth 1 -type f ! -name '*.part' ! -name '*.gz.age' -printf '%f\n' | sort |
        while read -r segment; do
            # A segment a quiet minute closed is mostly zeros: compressed, it weighs almost nothing.
            gzip -c "$segment" | age -R "$KEY" -o "$segment.gz.age.part"
            mv "$segment.gz.age.part" "$segment.gz.age"
            rm -f "$segment"
        done
    mapfile -t ready < <(find . -maxdepth 1 -type f -name '*.gz.age' -printf '%f\n' | sort)
    [ "${#ready[@]}" -gt 0 ] || exit 0
    # A segment sent twice (a retry after a crash) is skipped, never overwritten.
    gcloud storage cp --quiet --no-clobber "${ready[@]}" "gs://$BUCKET/wal/"
    rm -f "${ready[@]}"
    ;;
fetch)
    [ -n "${2:-}" ] && [ -f "${3:-}" ] && [ -n "${4:-}" ] ||
        { echo "wal.sh fetch <stamp> <age private key file> '<target time, 2026-09-30 14:05:00+00>'" >&2; exit 2; }
    [ -n "$BUCKET" ] || { echo "no PINECALL_BACKUP_BUCKET in /etc/pinecall/backup.env" >&2; exit 1; }
    stamp="$2"
    secret="$(realpath "$3")"
    target="$4"
    RESTORE="$SPOOL/restore"
    rm -rf "$RESTORE"
    install -d -m 0700 "$RESTORE"
    cd "$RESTORE"
    gcloud storage cp --quiet "gs://$BUCKET/$stamp/$stamp.base.tar.gz.age" \
        "gs://$BUCKET/$stamp/$stamp.pg_wal.tar.gz.age" .
    age -d -i "$secret" -o base.tar.gz "$stamp.base.tar.gz.age"
    age -d -i "$secret" -o pg_wal.tar.gz "$stamp.pg_wal.tar.gz.age"
    rm -f ./*.age
    start="$(tar -xOzf base.tar.gz backup_label | sed -n 's/^START WAL LOCATION: .*(file \([0-9A-F]*\))$/\1/p')"
    [ -n "$start" ] || { echo "the base backup of $stamp names no start segment" >&2; exit 1; }
    # Every timeline's history, and each segment from the base backup's first on.
    mapfile -t wanted < <(gcloud storage ls "gs://$BUCKET/wal/" | while read -r url; do
        segment="${url##*/}"
        segment="${segment%.gz.age}"
        case "$segment" in
            *.history) echo "$url" ;;
            *) [[ "$segment" < "$start" ]] || echo "$url" ;;
        esac
    done)
    [ "${#wanted[@]}" -eq 0 ] || printf '%s\n' "${wanted[@]}" | gcloud storage cp --quiet -I .
    for sealed in ./*.gz.age; do
        [ -e "$sealed" ] || continue
        age -d -i "$secret" "$sealed" | gunzip > "${sealed%.gz.age}"
        rm -f "$sealed"
    done
    # Appended to the restored data directory's postgresql.auto.conf; ALTER SYSTEM RESET clears it.
    {
        echo "restore_command = 'cp $RESTORE/%f %p'"
        echo "recovery_target_time = '$target'"
        echo "recovery_target_action = 'promote'"
    } > recovery.conf
    # Postgres (uid 999 in its image) reads them from inside its container.
    chown -R 999:999 "$RESTORE"
    echo "$RESTORE: the base backup of $stamp, and $(find . -maxdepth 1 -type f -name '0*' | wc -l) segments from $start"
    ;;
*)
    echo "wal.sh apply | ship | fetch <stamp> <age private key file> <target time>" >&2; exit 2 ;;
esac
