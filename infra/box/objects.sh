# The box's object store as rclone speaks it, sourced by backup.sh and wal.sh (as root): any
# S3-compatible endpoint, named in /etc/pinecall/backup.env by PINECALL_S3_ENDPOINT,
# PINECALL_S3_REGION and PINECALL_S3_ACCESS_KEY_ID, the secret sealed as the systemd credential
# PINECALL_S3_SECRET_ACCESS_KEY (`install.sh secret PINECALL_S3_SECRET_ACCESS_KEY`). The remote is
# `store:`, made from environment variables alone: no rclone config file, and no secret on the disk.
# Sets OBJECTS=on when the store, its secret and PINECALL_BACKUP_BUCKET are all there, off otherwise:
# a store half named is said on stderr (the unit's journal), and the backup stays on the box.
[ -f /etc/pinecall/backup.env ] && . /etc/pinecall/backup.env
OBJECTS=off
SEALED_SECRET=/etc/credstore.encrypted/PINECALL_S3_SECRET_ACCESS_KEY
IMPORTED_SECRET="${CREDENTIALS_DIRECTORY:-/nowhere}/PINECALL_S3_SECRET_ACCESS_KEY"
if [ -n "${PINECALL_BACKUP_BUCKET:-}" ] && [ -n "${PINECALL_S3_ENDPOINT:-}" ]; then
    if [ -z "${PINECALL_S3_REGION:-}" ] || [ -z "${PINECALL_S3_ACCESS_KEY_ID:-}" ]; then
        echo "backup.env names PINECALL_S3_ENDPOINT without PINECALL_S3_REGION and PINECALL_S3_ACCESS_KEY_ID: nothing leaves this box" >&2
    # A unit imports the credential; an operator's shell (wal.sh fetch) decrypts it from the store.
    elif [ -f "$IMPORTED_SECRET" ]; then
        RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY="$(cat "$IMPORTED_SECRET")"
    elif [ -f "$SEALED_SECRET" ]; then
        RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY="$(systemd-creds decrypt \
            --name=PINECALL_S3_SECRET_ACCESS_KEY "$SEALED_SECRET" -)"
    else
        echo "backup.env names PINECALL_S3_ENDPOINT and $SEALED_SECRET is not there: nothing leaves this box" >&2
    fi
fi
if [ -n "${RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY:-}" ]; then
    export RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY
    export RCLONE_CONFIG_STORE_TYPE=s3
    export RCLONE_CONFIG_STORE_PROVIDER=Other
    export RCLONE_CONFIG_STORE_ENDPOINT="$PINECALL_S3_ENDPOINT"
    export RCLONE_CONFIG_STORE_REGION="$PINECALL_S3_REGION"
    export RCLONE_CONFIG_STORE_ACCESS_KEY_ID="$PINECALL_S3_ACCESS_KEY_ID"
    # The bucket as a path, as every S3-compatible store answers it; and never created by the
    # box, whose key has no right to make one.
    export RCLONE_CONFIG_STORE_FORCE_PATH_STYLE=true
    export RCLONE_CONFIG_STORE_NO_CHECK_BUCKET=true
    OBJECTS=on
fi
