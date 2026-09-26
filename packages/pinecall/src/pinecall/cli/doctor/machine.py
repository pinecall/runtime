"""The machine under the runtime, asked three things: its disk, its fence, its certificate."""

from datetime import UTC, datetime

from pinecall.cli.doctor.probes import Probes
from pinecall.cli.doctor.report import Result, reason
from pinecall.settings import Settings

# Below this the box is minutes from a Postgres that refuses every write and a recorder that
# drops every file — both of them silent until a caller is the one to find out.
DISK_FLOOR_GB = 2.0
A_FULL_DISK = "a full disk stops Postgres writing and drops every recording"

# The unit that holds infra/box/nftables.conf up. With it down, every port the containers publish
# — Postgres is on loopback, but SIP, RTP and the SFU's media are not — is open to the internet.
THE_FENCE = "nftables"
NO_FENCE = "every port the media plane publishes is open to the world — `systemctl start nftables`"
NO_SYSTEMD = "no systemd on this machine: nothing to fence"

# Caddy renews a certificate thirty days before it ends and says so in its journal when it cannot
# (a DNS record moved, port 80 closed); two weeks out is the point a person has to look.
CERTIFICATE_FLOOR_DAYS = 14
NOT_RENEWED = "Caddy did not renew it — `journalctl -u caddy` says why"
NO_DOMAIN = "no PINECALL_DOMAIN: nothing is served under a name here"


def check_the_disk_has_room(settings: Settings, probes: Probes) -> Result:
    """Free space on the file system the recordings land on, which is the log's on a box too."""
    root = settings.recordings_root
    free = probes.disk_free_gb(root)
    if free < DISK_FLOOR_GB:
        return Result("disk", False, f"{free:.1f} GB free under {root} — {A_FULL_DISK}")
    return Result("disk", True, f"{free:.1f} GB free under {root}")


def check_the_fence_is_up(_settings: Settings, probes: Probes) -> Result:
    """Whether the firewall unit is active. A laptop with no systemd has nothing to fence."""
    active = probes.unit_active(THE_FENCE)
    if active is None:
        return Result("fence", True, NO_SYSTEMD)
    if not active:
        return Result("fence", False, f"{THE_FENCE} is not active — {NO_FENCE}")
    return Result("fence", True, f"{THE_FENCE} active")


def check_the_certificate_holds(settings: Settings, probes: Probes) -> Result:
    """The certificate the domain serves, and how long it has left; one with no domain is fine."""
    if not settings.domain:
        return Result("certificate", True, NO_DOMAIN)
    try:
        ends = probes.certificate_expiry(settings.domain)
    except Exception as failure:
        return Result("certificate", False, f"{settings.domain} — {reason(failure)}")
    days = (ends - datetime.now(UTC)).days
    said = f"{settings.domain} — ends {ends:%Y-%m-%d}, in {days} days"
    if days < CERTIFICATE_FLOOR_DAYS:
        return Result("certificate", False, f"{said} — {NOT_RENEWED}")
    return Result("certificate", True, said)
