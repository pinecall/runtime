"""The voice lab against a cluster: the generator configured, steps of SIP calls, a table each."""

# uv run --no-project python infra/lab/measure.py up
# uv run --no-project python infra/lab/measure.py run --calls 4,24,32 [--rate 1] [--kill-at 8]
#   up   the generator Terraform made beside the cluster (`-var lab=true`) configured: the vendors
#        faked, the providers row at them, an org with its quotas open and judging off, its key
#        (piped to the generator, never printed), the agent connected, a number hooked from the
#        generator's public address and approved
#   run  each step's calls placed at --rate a second from SIPp to the SIP node's public address,
#        held two minutes; one row per step from the call log and the pods' CPU; `--kill-at N`
#        resets the node of a scaled worker holding calls at the first step's N-th call, at once
# The world is production: its scaled workers are the ones KEDA grows, which is what is proven.

import argparse
import json
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parent.parent
ROOT = RUNTIME / "infra" / "terraform" / "environments" / "staging"
AGENT = RUNTIME.parent / "agents" / "examples" / "clinica-norte"
PROJECT, ZONE = "example-project", "us-central1-c"
CONTEXT = f"gke_{PROJECT}_{ZONE}_pinecall-staging"
URL = "https://staging.pinecall.io"
OPS_KEY = "pinecall-staging-ops-key"
NUMBER = "+15550100100"
ORG = "lab"
GEN = "pinecall-lab-gen"
QUOTAS = {
    "concurrent_calls": 1_000_000,
    "minutes": 100_000_000,
    "messages": 100_000_000,
    "llm_tokens": 100_000_000_000,
}

# A call speaks a turn every 10 s twelve times (caller.xml); the CPU is read inside that window,
# once every call is live and before the first hangs up.
CALL_S = 120
SETTLED_S = 45
WINDOW_S = CALL_S - 40
SAMPLE_S = 15

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
       (select count(*) from call_log l join c using (call) where l.type='error'),
       (select count(*) from call_log l join c using (call)
        where l.type='call.ended' and l.data->>'reason' = 'drained') from a
"""

CORE = (
    "pinecall-gateway",
    "pinecall-livekit",
    "pinecall-sip",
    "pinecall-postgres",
    "pinecall-redis",
)

HEADER = (
    "| calls at once | workers (pods) | per call | the core's services | turns answered "
    "| first audio p50 / p95 | ring to live | errors | drained | scaled workers · nodes |\n"
    "|---|---|---|---|---|---|---|---|---|---|"
)


def main() -> None:
    """Parse the verb and run it."""
    verbs = argparse.ArgumentParser(prog="measure.py")
    under = verbs.add_subparsers(dest="verb", required=True)
    under.add_parser("up", help="the generator configured, the agent connected, the number")
    run = under.add_parser("run", help="each step's calls placed and measured")
    run.add_argument("--calls", default="4,24,32", help="each step's calls at once")
    run.add_argument("--rate", type=int, default=1, help="calls placed a second")
    run.add_argument(
        "--kill-at", type=int, default=None, help="the first step's call a node dies at"
    )
    args = verbs.parse_args()
    lab = Lab()
    if args.verb == "up":
        lab.up()
    else:
        lab.run([int(step) for step in args.calls.split(",")], args.rate, args.kill_at)


class Lab:
    """The generator Terraform made, the cluster's gateway over HTTPS, its pods over kubectl."""

    def __init__(self) -> None:
        """The generator's addresses from Terraform; the ops key from Secret Manager, held here."""
        # Google's credentials are the gcloud login's, a short-lived token in the environment.
        token = _ran("gcloud", "auth", "print-access-token")
        made = json.loads(
            _ran(
                "env",
                f"GOOGLE_OAUTH_ACCESS_TOKEN={token}",
                "terraform",
                f"-chdir={ROOT}",
                "output",
                "-json",
                "lab_generator",
            )
        )
        if not made:
            raise SystemExit("no generator: apply staging with -var lab=true first")
        self.gen_internal, self.gen_public = made[0]["internal"], made[0]["public"]
        self.ops = _ran(
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={OPS_KEY}",
            f"--project={PROJECT}",
        )
        self.notes: list[str] = []

    # ── up ──

    def up(self) -> None:
        """The generator, the box's providers at its fakes, the org, the agent, the number."""
        self.ready()
        progress("the generator: the vendors faked, the caller's audio, the agent installed")
        self.copy(str(self.pack()), "/tmp/lab.tgz")
        self.copy(str(HERE / "generator.sh"), "/tmp/generator.sh")
        self.ssh("bash /tmp/generator.sh setup")
        progress("the providers row at the fakes; the org, its quotas, its vendors, its key")
        providers = (HERE / "providers.json").read_text().replace("FAKES_HOST", self.gen_internal)
        self.ops_call("PUT", "/v1/ops/providers", json.loads(providers))
        org = self.org()
        self.ops_call(
            "PUT",
            f"/v1/ops/orgs/{org}/quotas",
            {"env": "production", "quotas": {"limits": QUOTAS, "budget_usd": None, "lends": None}},
        )
        for vendor in ("anthropic", "deepgram", "cartesia"):
            self.ops_call(
                "PUT",
                f"/v1/ops/orgs/{org}/provider-keys/{vendor}",
                {"key": "not-a-key-the-lab-fakes-every-vendor"},
            )
        minted = self.ops_call(
            "POST", f"/v1/ops/orgs/{org}/keys", {"env": "production", "label": "lab-agent"}
        )
        key = str(minted["key"])
        _call("PUT", "/v1/org/judging", {"on": False}, key)
        progress("the agent under `pinecall start --prod`, the key by stdin")
        self.ssh(f"bash /tmp/generator.sh agent {URL}", given=key)
        progress(f"the number {NUMBER}, hooked from {self.gen_public}, approved")
        self.ssh(f"bash /tmp/generator.sh number {self.gen_public} {NUMBER}")
        waiting = self.ops_call("GET", "/v1/ops/carrier-networks?state=waiting", None)
        for ask in waiting if isinstance(waiting, list) else []:
            self.ops_call("POST", f"/v1/ops/carrier-networks/{ask['id']}/approve", None)
        path = _call("GET", f"/v1/numbers/{NUMBER}/path", None, key)
        print(json.dumps(path, indent=1))

    def pack(self) -> Path:
        """The lab's files and the agent's project as the generator's tarball, under .lab/."""
        staged = RUNTIME / ".lab" / "generator"
        _ran("rm", "-rf", str(staged))
        (staged / "agent").mkdir(parents=True)
        for name in ("fake_vendors.py", "caller.py", "caller.xml"):
            _ran("cp", str(HERE / name), str(staged))
        _ran("cp", "-R", str(AGENT / "agents"), str(AGENT / "tsconfig.json"), str(staged / "agent"))
        packed = RUNTIME / ".lab" / "generator.tgz"
        _ran("tar", "czf", str(packed), "-C", str(staged), ".")
        return packed

    def org(self) -> str:
        """The lab's org, made once."""
        listed = self.ops_call("GET", "/v1/ops/orgs", None)
        for row in listed if isinstance(listed, list) else listed.get("orgs", []):
            if row.get("slug") == ORG:
                return str(row["id"])
        return str(self.ops_call("POST", "/v1/ops/orgs", {"slug": ORG, "name": ORG})["id"])

    # ── run ──

    def run(self, steps: list[int], rate: int, kill_at: int | None) -> None:
        """Each step's calls placed and held; its row; the notes under the table."""
        rung = self.sip_address()
        rows = [
            self.step(calls, rung, rate, kill_at if n == 0 else None)
            for n, calls in enumerate(steps)
        ]
        print(f"\nstaging · SIP at {rung} · {rate} a second\n\n{HEADER}")
        for row in rows:
            print(row)
        for note in self.notes:
            print(f"\n{note}")

    def step(self, calls: int, rung: str, rate: int, kill_at: int | None) -> str:
        """That many calls at once, placed at the rate and kept up; the row the step reads."""
        progress(f"{calls} calls at once")
        t0 = time.time()
        sipp = (
            f"cd /home/lab && sudo rm -f caller_*; sudo timeout {CALL_S + calls + 150} sipp "
            f"{rung}:5060 -sf caller.xml -s {NUMBER} -i {self.gen_internal} "
            f"-mi {self.gen_internal} -m {calls} -l {calls} -r {rate} -max_socket 100000 "
            "-nostdin >/dev/null 2>&1; true"
        )
        caller = subprocess.Popen(
            (
                "gcloud",
                "compute",
                "ssh",
                GEN,
                f"--zone={ZONE}",
                f"--project={PROJECT}",
                "--command",
                sipp,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        waited = 0.0
        if kill_at is not None:
            waited = (kill_at - 1) / rate + 0.5
            time.sleep(waited)
            self.kill_a_node()
        time.sleep(max(0.0, calls / rate + SETTLED_S - waited))
        workers, core = self.cpu_over(WINDOW_S)
        caller.wait()
        t1 = time.time()
        rang, started, ring, user, agent, p50, p95, errors, drained = (
            value or "—" for value in self.psql(CALLS.format(t0=t0, t1=t1)).split("|")
        )
        live = int(started) if started.isdigit() else 0
        per = workers / live if live else 0.0
        return (
            f"| {calls} ({started} of {rang} started) | {workers:.2f} cores | {per:.2f} "
            f"| {core:.2f} cores | {agent} of {user} | {p50} / {p95} s | {ring} s | {errors} "
            f"| {drained} | {self.scaled()} |"
        )

    def cpu_over(self, seconds: float) -> tuple[float, float]:
        """The workers' and the core services' cores, averaged over samples of the pods' metrics."""
        samples: list[tuple[float, float]] = []
        end = time.time() + seconds
        while time.time() < end:
            pods = json.loads(
                self.kubectl("get", "--raw", "/apis/metrics.k8s.io/v1beta1/namespaces/default/pods")
            )
            workers = core = 0.0
            for pod in pods["items"]:
                cores = sum(_cores(c["usage"]["cpu"]) for c in pod["containers"])
                name = pod["metadata"]["name"]
                if name.startswith(("worker-core-production", "worker-scaled-production")):
                    workers += cores
                elif name.startswith(CORE):
                    core += cores
            samples.append((workers, core))
            time.sleep(SAMPLE_S)
        return (
            sum(s[0] for s in samples) / len(samples),
            sum(s[1] for s in samples) / len(samples),
        )

    def scaled(self) -> str:
        """KEDA's scaled workers of production now, and the workers pool's nodes."""
        replicas = (
            self.kubectl(
                "get", "deployment", "worker-scaled-production", "-o", "jsonpath={.status.replicas}"
            )
            or "0"
        )
        nodes = self.kubectl("get", "nodes", "-l", "pinecall.io/pool=workers", "-o", "name")
        return f"{replicas} · {len(nodes.split())}"

    # As a machine that dies: a hard reset, no pod stopped, no socket closed, nothing told.
    def kill_a_node(self) -> None:
        """The node of the first scaled worker reset at once, and the note of it."""
        node = self.kubectl(
            "get",
            "pods",
            "-l",
            "app=worker,pool=workers",
            "-o",
            "jsonpath={.items[0].spec.nodeName}",
        )
        if not node:
            self.notes.append("no scaled worker to kill: every call was on the core node")
            return
        progress(f"{node} reset at once")
        _ran(
            "gcloud",
            "compute",
            "instances",
            "reset",
            node,
            f"--zone={ZONE}",
            f"--project={PROJECT}",
        )
        self.notes.append(f"{node} (a scaled worker's node) reset at once during the first step")

    def sip_address(self) -> str:
        """The public address of the node the SIP pod runs on, where a carrier sends INVITEs."""
        node = self.kubectl(
            "get", "pods", "-l", "app=pinecall-sip", "-o", "jsonpath={.items[0].spec.nodeName}"
        )
        return self.kubectl(
            "get",
            "node",
            node,
            "-o",
            "jsonpath={.status.addresses[?(@.type=='ExternalIP')].address}",
        )

    # ── the wire to them ──

    def psql(self, query: str) -> str:
        """One query on the cluster's Postgres, as its superuser on the pod's socket."""
        return self.kubectl(
            "exec",
            "pinecall-postgres-1",
            "-c",
            "postgres",
            "--",
            "psql",
            "-d",
            "pinecall",
            "-At",
            "-F",
            "|",
            "-c",
            query,
        )

    def kubectl(self, *argv: str) -> str:
        """One kubectl command on staging; its stdout."""
        return _ran("kubectl", f"--context={CONTEXT}", *argv)

    def ops_call(self, method: str, path: str, body: Any) -> Any:
        """One door of the operator's, with the ops key."""
        return _call(method, path, body, self.ops)

    def ready(self) -> None:
        """The generator answers ssh and its first boot is done."""
        for _ in range(60):
            done = subprocess.run(
                (
                    "gcloud",
                    "--quiet",
                    "compute",
                    "ssh",
                    GEN,
                    f"--zone={ZONE}",
                    f"--project={PROJECT}",
                    "--command",
                    "cloud-init status --wait >/dev/null 2>&1; echo ok",
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            if done.stdout.strip() == "ok":
                return
            time.sleep(5)
        raise SystemExit(f"{GEN}: cloud-init never finished")

    def ssh(self, command: str, *, given: str | None = None) -> None:
        """A command on the generator, `given` on its stdin; its output printed."""
        done = subprocess.run(
            (
                "gcloud",
                "--quiet",
                "compute",
                "ssh",
                GEN,
                f"--zone={ZONE}",
                f"--project={PROJECT}",
                "--command",
                command,
            ),
            input=given,
            capture_output=True,
            text=True,
            check=False,
        )
        if done.stdout.strip():
            print(done.stdout.strip())
        if done.returncode != 0:
            raise SystemExit(f"{GEN}: {shlex.quote(command)}: {done.stderr.strip()[-600:]}")

    def copy(self, path: str, there: str) -> None:
        """A local file onto the generator."""
        _ran(
            "gcloud",
            "--quiet",
            "compute",
            "scp",
            path,
            f"{GEN}:{there}",
            f"--zone={ZONE}",
            f"--project={PROJECT}",
        )


def progress(text: str) -> None:
    """One line of progress."""
    print(f"→ {text}", flush=True)


def _call(method: str, path: str, body: Any, key: str) -> Any:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{URL}{path}", data=data, method=method)
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("pinecall-env", "production")
    if data is not None:
        request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as answer:
            text = answer.read().decode()
    except urllib.error.HTTPError as refused:
        raise SystemExit(
            f"{method} {path}: {refused.code} {refused.read().decode()[:400]}"
        ) from None
    return json.loads(text) if text else None


def _ran(*argv: str) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SystemExit(
            f"{argv[0]} {argv[1] if len(argv) > 1 else ''}: {done.stderr.strip()[-600:]}"
        )
    return done.stdout.strip()


def _cores(value: str) -> float:
    if value.endswith("n"):
        return int(value[:-1]) / 1e9
    if value.endswith("u"):
        return int(value[:-1]) / 1e6
    if value.endswith("m"):
        return int(value[:-1]) / 1e3
    return float(value)


if __name__ == "__main__":
    sys.exit(main())
