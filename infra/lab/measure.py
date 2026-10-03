"""The voice lab in one command: a box, a generator and worker machines made, a table of calls."""

# uv run --no-project python infra/lab/measure.py measure --box e2-standard-2 \
#     --worker e2-standard-2
#   up       the box and the generator made by Terraform (infra/terraform/environments/lab), the
#            box up from this checkout's wheel, both configured (configure.sh)
#   run      `--workers` machines of that type made and joined (`cell join-worker`), the calls
#            ramped, one row per step, the machines destroyed; `--box` resizes the box first;
#            `--kill-at N` powers the last machine off at once at the first step's N-th call
#   down     every lab machine destroyed (`terraform destroy`)
#   measure  up, run, down
# Every secret goes from one machine to the other through a pipe between two ssh processes, never
# through this process's output. The project and zone are the lab root's (terraform.tfvars).

import argparse
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parent.parent
LAB_ROOT = RUNTIME / "infra" / "terraform" / "environments" / "lab"
AGENT = RUNTIME.parent / "agents" / "examples" / "clinica-norte"

BOX, GEN, WORKER = "pinecall-lab-box", "pinecall-lab-gen", "pinecall-lab-wk-{}"
NAMES = "lab.pinecall.invalid,sandbox.lab.pinecall.invalid"
NUMBER = "+15550100100"
CONFIGURE = "bash /tmp/configure.sh"

# A call speaks a turn every 10 s twelve times (caller.xml); the CPU is read inside that window,
# once every call is live and before the first hangs up.
CALL_S = 120
SETTLED_S = 45
WINDOW_S = CALL_S - 40

# Every column a step's row has, read from the call log of the calls that rang in its window.
CALLS = """
with c as (select call, ts from call_log where type='call.ringing' and ts between {t0} and {t1}),
 s as (select l.ts - c.ts setup from call_log l join c using (call) where l.type='call.started'),
 a as (select (l.data->'metrics'->>'e2e_latency')::float e2e from call_log l join c using (call)
       where l.type='turn.agent' and l.data->'metrics' ? 'e2e_latency')
select (select count(*) from c), (select count(*) from s),
       (select round(percentile_cont(0.5) within group (order by setup)::numeric, 1) from s),
       (select count(*) from call_log l join c using (call) where l.type='turn.user'), count(*),
       round(percentile_cont(0.5) within group (order by e2e)::numeric, 2),
       round(percentile_cont(0.95) within group (order by e2e)::numeric, 2),
       (select count(*) from call_log l join c using (call) where l.type='error') from a
"""

PSQL = "sudo podman exec pinecall-postgres psql -U pinecall -d pinecall -At -F '|' -c"

# A machine's busy CPU seconds (/proc/stat's user, nice, system, irq, softirq), and the clock.
BUSY = r"""date +%s.%N; awk '/^cpu /{print ($2+$3+$4+$7+$8)/100}' /proc/stat"""

# What the machine killed with calls on it did to them, from the call log of the step's calls and
# the gateways' journal: the calls it held, told and ended as drained; every call that started.
KILLED = """
with c as (select call, ts from call_log where type='call.ringing' and ts between {t0} and {t1}),
 s as (select l.ts - c.ts setup from call_log l join c using (call) where l.type='call.started')
select (select count(*) from c), (select count(*) from s),
       (select count(*) from call_log l join c using (call)
        where l.type='call.ended' and l.data->>'reason' = 'drained'),
       (select round(max(setup)::numeric, 1) from s)
"""

GATEWAYS_SAID = (
    "sudo journalctl -u 'pinecall-gateway@*' --since @{t0:.0f} --until @{t1:.0f} -o cat "
    "--no-pager | grep -c {said} || true"
)

# A machine killed while another takes the calls: one alone would leave nobody to take them.
KILLED_AMONG = 2

# channels/offers.py's OFFERED_AGAIN_AFTER_S: this runs with no project, so it says it again.
OFFERED_AGAIN_S = 12

POWER_OFF_TAKES_S = 30

# Abrupt as a machine that dies: no unit stopped, no socket closed, nothing told.
POWERED_OFF = "sudo sh -c 'echo 1 > /proc/sys/kernel/sysrq; echo o > /proc/sysrq-trigger'"

HEADER = (
    "| calls at once | worker machine | per call | box | turns answered "
    "| first audio p50 / p95 | ring to live | errors |\n|---|---|---|---|---|---|---|---|"
)


def main() -> None:
    """Parse the verb and run it."""
    verbs = argparse.ArgumentParser(prog="measure.py")
    under = verbs.add_subparsers(dest="verb", required=True)
    up = under.add_parser("up", help="the box and the generator, configured")
    up.add_argument("--box", default="e2-standard-4", help="the box's machine type")
    run = under.add_parser("run", help="a worker machine joined and the calls ramped")
    measure = under.add_parser("measure", help="up, run, down")
    for verb in (run, measure):
        verb.add_argument("--worker", required=True, help="the worker machines' type")
        verb.add_argument("--workers", type=int, default=1, help="how many worker machines")
        verb.add_argument("--calls", default="4,6,8,10,12", help="each step's calls at once")
        verb.add_argument("--seats", type=int, default=None, help="unset: four per vCPU")
        verb.add_argument("--rate", type=int, default=1, help="calls placed a second")
        verb.add_argument(
            "--kill-at",
            type=int,
            default=None,
            help="the first step's call the last machine dies at",
        )
    run.add_argument("--box", default=None, help="resize the box to this type first")
    measure.add_argument("--box", default="e2-standard-4", help="the box's machine type")
    under.add_parser("down", help="every lab machine destroyed")
    args = verbs.parse_args()
    if getattr(args, "kill_at", None) is not None and args.workers < KILLED_AMONG:
        verbs.error("--kill-at kills the last of two machines or more: --workers 2")
    lab = Lab(getattr(args, "rate", 1))
    if args.verb in ("up", "measure"):
        lab.up(args.box)
    if args.verb in ("run", "measure"):
        steps = [int(step) for step in args.calls.split(",")]
        shape = Shape(args.worker, args.workers, args.seats, args.kill_at)
        lab.run(shape, steps, args.box)
    if args.verb in ("down", "measure"):
        lab.down()


@dataclass(frozen=True)
class Shape:
    """The worker machines under test: their type, how many, their seats, and the one killed."""

    worker_type: str
    workers: int
    seats: int | None
    kill_at: int | None

    def names(self) -> list[str]:
        """Each worker machine's name, the killed one last."""
        return [WORKER.format(number) for number in range(1, self.workers + 1)]


class Lab:
    """The lab's machines, as the lab's Terraform root holds them, reached through gcloud."""

    def __init__(self, rate: int) -> None:
        """The root initialized; nothing made yet; each step places `rate` calls a second."""
        self.rate = rate
        self.wheel: Path | None = None
        self.notes: list[str] = []
        self.where: tuple[str, ...] = ()
        terraform("init", "-input=false")

    # ── up ──

    def up(self, box_type: str) -> None:
        """The box and the generator made, the box up from this checkout, both configured."""
        self.wheel = _wheel()
        progress(f"the box ({box_type}) and the generator, by Terraform")
        made = self.applied(box_type, None)
        box, gen = made["box_internal_address"], made["generator_internal_address"]
        for name in (BOX, GEN):
            self.ready(name)
            self.copy(self.wheel, name, f"/tmp/{self.wheel.name}")
            self.copy(HERE / "configure.sh", name, "/tmp/configure.sh")
        # A box already up keeps its release: `up` run again goes on from the generator.
        gateway = "systemctl is-active pinecall-gateway@8080"
        if self.ssh(BOX, gateway, quiet=True, check=False) != "active":
            progress("the box up from the wheel (~10 min)")
            wheel = f"/tmp/{self.wheel.name}"
            self.ssh(
                BOX,
                f"sudo uvx --from {wheel} pinecall-runtime box up "
                f"--domains {NAMES} --package {wheel}",
            )
        progress("the generator: the vendors faked, the caller, the agent, the bucket")
        self.copy_bytes(_lab_files(), GEN, "/tmp/lab.tgz")
        self.ssh(GEN, f"{CONFIGURE} generator {gen}")
        progress("the box: providers at the fakes, the org, its keys, the store, the fence")
        self.copy_bytes(_providers(gen).encode(), BOX, "/tmp/providers.json")
        self.ssh(BOX, f"{CONFIGURE} box {gen}")
        # The store's secret and the agent's key go machine to machine, never through here.
        sealing = "sudo /opt/pinecall/infra/box/install.sh secret PINECALL_S3_SECRET_ACCESS_KEY"
        self.piped(GEN, "sudo cat /home/lab/s3.secret", BOX, sealing)
        self.piped(GEN, "sudo cat /home/lab/s3.user", BOX, f"{CONFIGURE} store {gen}")
        self.piped(BOX, "sudo cat /root/lab-agent.key", GEN, f"{CONFIGURE} agent-env {box}")
        self.ssh(GEN, f"{CONFIGURE} agent")
        progress(f"the number {NUMBER} hooked from the generator's public address, approved")
        self.ssh(GEN, f"{CONFIGURE} number {made['generator_public_address']} {NUMBER}")
        self.ssh(BOX, f"{CONFIGURE} admit {NUMBER}")
        self.ssh(BOX, "sudo systemctl stop 'pinecall-worker-a@*' 'pinecall-worker-b@*'")

    # ── run ──

    def run(self, shape: Shape, steps: list[int], box_type: str | None) -> None:
        """The worker machines made and joined, each step's calls ramped, one row each."""
        wheel = self.wheel = self.wheel or _wheel()
        before = outputs()
        sized: str = box_type or before["box_type"]
        progress(f"{shape.workers} x {shape.worker_type}, the box {sized}, by Terraform")
        made = self.applied(sized, shape)
        if sized != before["box_type"]:
            self.ready(BOX)
            self.ssh(BOX, "until systemctl is-active -q pinecall-gateway@8080; do sleep 2; done")
        box, gen = made["box_internal_address"], made["generator_internal_address"]
        rung = made["box_public_address"]
        machines = dict(zip(shape.names(), made["worker_internal_addresses"], strict=True))
        self.ssh(BOX, "sudo systemctl stop 'pinecall-worker-a@*' 'pinecall-worker-b@*'")
        for name, address in machines.items():
            self.joined(wheel, name, address, (box, shape.seats))
        self.ssh(BOX, "sudo pinecall-runtime fleet list 2>/dev/null | grep pinecall-sandbox")
        killed = (shape.names()[-1], shape.kill_at) if shape.kill_at is not None else None
        rows = [
            self.step(calls, rung, gen, killed if number == 0 else None)
            for number, calls in enumerate(steps)
        ]
        print(f"\nbox {sized} · {shape.workers} x {shape.worker_type}\n\n{HEADER}")
        for row in rows:
            print(row)
        for note in self.notes:
            print(f"\n{note}")
        alive = [name for name in machines if killed is None or name != killed[0]]
        for name in alive:
            self.kept_journal(name)
            # As the fleet loop lets a machine go: the worker stopped first, so it closes its link
            # to LiveKit. A machine destroyed with its worker up stays registered in livekit-server
            # for 15 minutes or more (the gateway never offers it a call once unheard for 12 s).
            self.ssh(name, "sudo systemctl stop 'pinecall-worker@*'")
        for address in machines.values():
            self.ssh(BOX, f"sudo pinecall-runtime cell forget-worker {address}")
        progress("the worker machines destroyed")
        self.applied(sized, None)

    def joined(self, wheel_file: Path, name: str, address: str, at: tuple[str, int | None]) -> None:
        """A worker machine joined to the box's sandbox fleet from the wheel, with its seats."""
        box, seats = at
        self.ready(name)
        self.copy(wheel_file, name, f"/tmp/{wheel_file.name}")
        self.ssh(BOX, f"sudo pinecall-runtime cell allow-worker {address}")
        counted = "" if seats is None else f" --calls {seats}"
        wheel = f"/tmp/{wheel_file.name}"
        self.piped(
            BOX,
            "sudo pinecall-runtime cell worker-credentials sandbox",
            name,
            f"sudo uvx --from {wheel} pinecall-runtime cell join-worker "
            f"{box} sandbox --package {wheel}{counted}",
        )

    # The evidence of a step is on the machine the step destroys: its worker's journal and the
    # settings it ran with are kept here, under .lab/, before it goes.
    def kept_journal(self, name: str) -> None:
        """The worker machine's journal and fleet.env, saved under .lab/ and named."""
        kept = RUNTIME / ".lab" / f"{name}-{time.strftime('%Y%m%dT%H%M%S')}.log"
        kept.parent.mkdir(exist_ok=True)
        settings = self.ssh(name, "sudo cat /etc/pinecall/fleet.env", quiet=True)
        journal = self.ssh(
            name, "sudo journalctl -u 'pinecall-worker*' --no-pager -o short-iso", quiet=True
        )
        kept.write_text(f"# /etc/pinecall/fleet.env\n{settings}\n\n# journal\n{journal}\n")
        progress(f"the worker's journal kept at {kept.relative_to(RUNTIME)}")

    # SIP goes to the box's public address, as a carrier's does: within the VPC the generator's
    # source is then its public address, the one the number's fence admits.
    def step(self, calls: int, rung: str, gen: str, dies: tuple[str, int] | None) -> str:
        """That many calls at once, placed at the lab's rate, kept up; the row the step reads."""
        progress(f"{calls} calls at once")
        t0 = float(self.ssh(BOX, "date +%s", quiet=True))
        sipp = (
            f"cd /home/lab && sudo rm -f caller_*; sudo timeout {CALL_S + calls + 150} sipp "
            f"{rung}:5060 -sf caller.xml -s {NUMBER} -i {gen} -mi {gen} -m {calls} -l {calls} "
            f"-r {self.rate} -max_socket 100000 -nostdin >/dev/null 2>&1; true"
        )
        caller = self.background(GEN, sipp)
        waited = 0.0
        if dies is not None:
            name, at = dies
            waited = (at - 1) / self.rate + 0.5
            time.sleep(waited)
            progress(f"{name} powered off at call {at}")
            self.powered_off(name)
        measured = WORKER.format(1)
        time.sleep(max(0.0, calls + SETTLED_S - waited))
        before = (self.busy(measured), self.busy(BOX))
        time.sleep(WINDOW_S)
        after = (self.busy(measured), self.busy(BOX))
        caller.wait()
        t1 = float(self.ssh(BOX, "date +%s", quiet=True))
        worker_cores, box_cores = (_cores(before[i], after[i]) for i in range(2))
        query = shlex.quote(CALLS.format(t0=t0, t1=t1))
        rang, started, ring, user, agent, p50, p95, errors = (
            value or "—" for value in self.ssh(BOX, f"{PSQL} {query}", quiet=True).split("|")
        )
        if dies is not None:
            self.notes.append(self.said_of_the_kill(dies, t0, t1))
        # Per call that started: a call that never rang costs nothing.
        live = int(started) if started.isdigit() else 0
        per = worker_cores / live if live else 0.0
        return (
            f"| {calls} ({started} of {rang} started) | {worker_cores:.2f} cores | {per:.2f} "
            f"| {box_cores:.2f} cores | {agent} of {user} | {p50} / {p95} s | {ring} s | {errors} |"
        )

    def said_of_the_kill(self, dies: tuple[str, int], t0: float, t1: float) -> str:
        """What the killed machine did to the step's calls, a paragraph for under the table."""
        query = shlex.quote(KILLED.format(t0=t0, t1=t1))
        rang, started, drained, slowest = (
            value or "—" for value in self.ssh(BOX, f"{PSQL} {query}", quiet=True).split("|")
        )
        said = {
            what: self.ssh(
                BOX, GATEWAYS_SAID.format(t0=t0, t1=t1, said=shlex.quote(text)), quiet=True
            )
            for what, text in (
                ("again", "no worker opened it in"),
                ("overflow", "the overflow says the sentence"),
                ("gone", "its worker went away"),
            )
        }
        return (
            f"{dies[0]} powered off at call {dies[1]}: {started} of {rang} calls started; "
            f"{said['gone']} lost their worker, {drained} ended as drained (told); "
            f"{said['again']} offered again after {OFFERED_AGAIN_S:.0f} s; "
            f"{said['overflow']} to the overflow; ring to live at most {slowest} s"
        )

    # The machine goes mid-command, so its ssh never ends by itself: it is cut after a while.
    def powered_off(self, name: str) -> None:
        """The machine powered off at once, as one that dies."""
        argv = ("gcloud", "compute", "ssh", name, *self.where, "--command", POWERED_OFF)
        with contextlib.suppress(subprocess.TimeoutExpired):
            subprocess.run(argv, capture_output=True, timeout=POWER_OFF_TAKES_S, check=False)

    def busy(self, name: str) -> tuple[float, float]:
        """The machine's clock and busy CPU seconds."""
        clock, busy = self.ssh(name, BUSY, quiet=True).split()
        return float(clock), float(busy)

    # ── down ──

    def down(self) -> None:
        """Every lab machine destroyed, its disk with it, and the lab's firewall rule."""
        progress("every lab machine destroyed, by Terraform")
        terraform("destroy", "-auto-approve", "-input=false")

    # ── machines ──

    def applied(self, box_type: str, shape: Shape | None) -> dict[str, Any]:
        """The lab at these sizes (worker machines only when a shape is given); its outputs."""
        sizes = ["-var", f"box_type={box_type}"]
        if shape is not None:
            sizes += [
                "-var",
                f"worker_type={shape.worker_type}",
                "-var",
                f"workers={shape.workers}",
            ]
        terraform("apply", "-auto-approve", "-input=false", *sizes)
        made = outputs()
        self.where = ("--project", made["project"], "--zone", made["zone"])
        return made

    def ready(self, name: str) -> None:
        """The machine answers ssh and its first boot is done."""
        done = "cloud-init status --wait >/dev/null 2>&1; echo ok"
        for _ in range(60):
            if self.ssh(name, done, quiet=True, check=False) == "ok":
                return
            time.sleep(5)
        raise SystemExit(f"{name}: cloud-init never finished")

    # ── the wire to them ──

    def ssh(self, name: str, command: str, *, quiet: bool = False, check: bool = True) -> str:
        """A command on the machine; its stdout, which is printed unless quiet."""
        argv = ("gcloud", "--quiet", "compute", "ssh", name, *self.where, "--command", command)
        done = subprocess.run(argv, capture_output=True, text=True, check=False)
        if check and done.returncode != 0:
            raise SystemExit(f"{name}: {done.stderr.strip()[-600:]}")
        if not quiet and done.stdout.strip():
            print(done.stdout.strip())
        return done.stdout.strip()

    def background(self, name: str, command: str) -> subprocess.Popen[bytes]:
        """A command on the machine, left running; its output dropped."""
        argv = ("gcloud", "compute", "ssh", name, *self.where, "--command", command)
        return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def piped(self, source: str, giving: str, target: str, taking: str) -> None:
        """One machine's stdout into another's stdin: what a secret travels by."""
        giver = subprocess.Popen(
            ("gcloud", "compute", "ssh", source, *self.where, "--command", giving),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        taker = subprocess.run(
            ("gcloud", "compute", "ssh", target, *self.where, "--command", taking),
            stdin=giver.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
        giver.wait()
        if giver.returncode != 0 or taker.returncode != 0:
            raise SystemExit(f"{source} → {target}: {taker.stderr.strip()[-600:]}")

    def copy(self, path: Path, name: str, there: str) -> None:
        """A local file onto the machine."""
        argv = ("gcloud", "--quiet", "compute", "scp", str(path), f"{name}:{there}", *self.where)
        subprocess.run(argv, capture_output=True, check=True)

    def copy_bytes(self, data: bytes, name: str, there: str) -> None:
        """Bytes onto the machine, through a file of this run's."""
        with tempfile.NamedTemporaryFile(delete=False) as kept:
            kept.write(data)
        self.copy(Path(kept.name), name, there)


def progress(text: str) -> None:
    """One line of progress."""
    print(f"→ {text}", flush=True)


# Google's credentials are the gcloud login's, handed over as a short-lived token in the
# environment, fresh for each command: a run outlives an hour's token.
def terraform(*argv: str) -> None:
    """One terraform command on the lab's root; its output shown, a failure the end of the run."""
    done = subprocess.run(
        ("terraform", f"-chdir={LAB_ROOT}", *argv), env=_google_env(), check=False
    )
    if done.returncode != 0:
        raise SystemExit(
            f"terraform {argv[0]} failed: the lab may be half made; `down` destroys it"
        )


def outputs() -> dict[str, Any]:
    """The lab root's outputs, by name; a null one is absent."""
    argv = ("terraform", f"-chdir={LAB_ROOT}", "output", "-json")
    shown = subprocess.run(argv, capture_output=True, text=True, env=_google_env(), check=True)
    values = json.loads(shown.stdout)
    if "box_type" not in values:
        raise SystemExit("the lab is not up: `up` first")
    return {name: item["value"] for name, item in values.items() if item["value"] is not None}


def _google_env() -> dict[str, str]:
    login = ("gcloud", "auth", "print-access-token")
    token = subprocess.run(login, capture_output=True, text=True, check=True).stdout.strip()
    return {**os.environ, "GOOGLE_OAUTH_ACCESS_TOKEN": token}


def _cores(before: tuple[float, float], after: tuple[float, float]) -> float:
    return (after[1] - before[1]) / (after[0] - before[0])


def _wheel() -> Path:
    progress("the wheel of this checkout")
    subprocess.run(
        ("uv", "build", "--wheel", "--quiet", "--out-dir", "dist"), cwd=RUNTIME, check=True
    )
    return max((RUNTIME / "dist").glob("pinecall-*.whl"), key=lambda path: path.stat().st_mtime)


def _lab_files() -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name in ("fake_vendors.py", "caller.py", "caller.xml"):
            tar.add(HERE / name, arcname=name)
        tar.add(AGENT / "agents", arcname="agent/agents")
        tar.add(AGENT / "tsconfig.json", arcname="agent/tsconfig.json")
    return out.getvalue()


def _providers(gen: str) -> str:
    return (HERE / "providers.json").read_text().replace("FAKES_HOST", gen)


if __name__ == "__main__":
    sys.exit(main())
