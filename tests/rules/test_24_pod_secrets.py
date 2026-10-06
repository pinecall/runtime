"""Rule 24: a worker's pod is handed no database, no vault key and no operator's key."""

import re
from pathlib import Path

from tests.rules.tree import ROOT

TEMPLATES = ROOT / "infra" / "charts" / "pinecall" / "templates"

# What only the gateway (and the migrations, the retention) may be handed.
THE_GATEWAYS = (
    "DATABASE_URL",
    "PINECALL_VAULT_KEY",
    "PINECALL_OPS_KEY",
    "PINECALL_TOKEN_KEY",
    "PINECALL_REDIS_URL",
)

# The helper that carries them, and the templates that must never include it.
GATEWAY_ENV = re.compile(r'include "pinecall\.env"')
A_WORKERS = ("workers.yaml", "overflow.yaml")

INCLUDED = (
    "{file}: includes pinecall.env, which hands a worker the database, the vault and the ops key"
)
NAMED = "{file}: names {name}, which is the gateway's alone"


def offences(templates: Path) -> list[str]:
    """Return every worker template that is handed what the gateway alone may hold."""
    found: list[str] = []
    for name in A_WORKERS:
        text = (templates / name).read_text(encoding="utf-8")
        if GATEWAY_ENV.search(text):
            found.append(INCLUDED.format(file=name))
        found.extend(NAMED.format(file=name, name=key) for key in THE_GATEWAYS if key in text)
    helpers = (templates / "_helpers.tpl").read_text(encoding="utf-8")
    worker_env = helpers[helpers.index('define "pinecall.workerEnv"') :]
    found.extend(
        NAMED.format(file="_helpers.tpl workerEnv", name=k) for k in THE_GATEWAYS if k in worker_env
    )
    return found


def test_no_worker_pod_is_handed_the_gateways_secrets() -> None:
    assert offences(TEMPLATES) == []


def test_the_rule_catches_a_worker_template_that_includes_the_gateways_env(tmp_path: Path) -> None:
    (tmp_path / "workers.yaml").write_text('env:\n  {{- include "pinecall.env" $ }}\n')
    (tmp_path / "overflow.yaml").write_text("env:\n  - name: PINECALL_OPS_KEY\n")
    (tmp_path / "_helpers.tpl").write_text(
        '{{- define "pinecall.workerEnv" -}}\n- name: LIVEKIT_URL\n{{- end -}}\n'
    )
    assert offences(tmp_path) == [
        INCLUDED.format(file="workers.yaml"),
        NAMED.format(file="overflow.yaml", name="PINECALL_OPS_KEY"),
    ]
