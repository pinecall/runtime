"""The matrix as one HTML page: a table per judge, the findings, and what each call cost."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any, cast

from pinecall.evals.matrix import Matrix, Run

# One file, no stylesheet to fetch and no script to run: a report is opened from a terminal, mailed
# to somebody, or committed beside the run it is about, and any of those may have no network.
STYLE = """
  body { font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 2rem; color: #111; }
  h1 { font-size: 1.4rem; } h2 { font-size: 1.05rem; margin-top: 2rem; }
  table { border-collapse: collapse; margin: .6rem 0 1.4rem; }
  th, td { border: 1px solid #ddd; padding: .35rem .7rem; text-align: left; }
  th { background: #f6f6f6; font-weight: 600; }
  td.held { background: #eaf7ee; } td.broken { background: #fdecec; }
  td.absent { color: #999; }
  li { margin-bottom: .4rem; } code { background: #f4f4f4; padding: 0 .25rem; }
  p.note { color: #666; }
"""

# A judgment always carries its reasoning, whoever answered it: a policy writes the seqs for free
# and a model is asked for one sentence. An empty one is a judge that broke its own contract.
NO_REASON = "no reason was written"


def as_html(matrix: Matrix, *, title: str = "Pinecall evals") -> str:
    """The whole matrix as one page: every judge's table, then every finding, then the calls."""
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            f"<title>{escape(title)}</title><style>{STYLE}</style>",
            "</head><body>",
            f"<h1>{escape(title)}</h1>",
            *(_a_table(matrix, metric) for metric in matrix.metrics),
            _the_findings(matrix),
            _the_calls(matrix),
            "</body></html>",
        ]
    )


def _a_table(matrix: Matrix, metric: str) -> str:
    """One judge, models down the side and goldens across the top, a score in every cell."""
    header = "".join(f"<th>{escape(golden)}</th>" for golden in matrix.goldens)
    rows = "".join(_a_row(matrix, metric, model) for model in matrix.models)
    return f"<h2>{escape(metric)}</h2><table><tr><th>model</th>{header}</tr>{rows}</table>"


def _a_row(matrix: Matrix, metric: str, model: str) -> str:
    """One model's row: what this judge answered about that model on each golden."""
    cells = "".join(_a_cell(matrix.at(model, golden), metric) for golden in matrix.goldens)
    return f"<tr><th>{escape(model)}</th>{cells}</tr>"


def _a_cell(run: Run | None, metric: str) -> str:
    """One score, or a dash when that golden was never run under that model."""
    score = run.at(metric) if run else None
    if score is None:
        return '<td class="absent">—</td>'
    held = "held" if score.passed else "broken"
    return f'<td class="{held}">{score.score:.2f}</td>'


# The findings are the report: a matrix of green cells says nothing a person needs to read, and a
# red one is only useful next to the sentence that says which line of the log made it red.
def _the_findings(matrix: Matrix) -> str:
    """Every judge that did not hold, with the sentence it wrote when it did not."""
    failures = matrix.failures()
    if not failures:
        return "<h2>Findings</h2><p class='note'>Every judge held on every golden.</p>"
    items = "".join(
        f"<li><code>{escape(run.model)}</code> · <code>{escape(run.golden)}</code> · "
        f"<b>{escape(score.metric)}</b>: {escape(score.reason or NO_REASON)}</li>"
        for run, score in failures
    )
    return f"<h2>Findings</h2><ul>{items}</ul>"


def _the_calls(matrix: Matrix) -> str:
    """What each call itself carried, read off `call.summary` and never computed here."""
    rows = "".join(_a_call_row(run) for run in matrix.runs)
    asked = matrix.judge_calls
    return (
        "<h2>Calls</h2><table><tr><th>model</th><th>golden</th><th>outcome</th>"
        "<th>turns</th><th>duration (s)</th><th>cost (EUR)</th></tr>"
        f"{rows}</table>"
        f"<p class='note'>Judging this matrix put {asked} question(s) to a model. "
        "Every number on this page is read off the call's own <code>call.summary</code>.</p>"
    )


def _a_call_row(run: Run) -> str:
    """One call as its summary states it. A call with no summary yet says so in every column."""
    summary: Mapping[str, Any] = run.summary or {}
    return (
        f"<tr><td>{escape(run.model)}</td><td>{escape(run.golden)}</td>"
        f"<td>{escape(str(summary.get('outcome', '—')))}</td>"
        f"<td>{escape(str(summary.get('turns', '—')))}</td>"
        f"<td>{escape(_seconds(summary.get('duration_s')))}</td>"
        f"<td>{escape(_euros(summary.get('cost')))}</td></tr>"
    )


def _seconds(value: Any) -> str:
    """A duration as a person reads it, or a dash when the call never wrote one."""
    return f"{value:.1f}" if isinstance(value, int | float) else "—"


def _euros(cost: Any) -> str:
    """What the call cost in provider fees, straight out of `call.summary.cost.eur`."""
    if isinstance(cost, Mapping) and isinstance(
        eur := cast("Mapping[str, Any]", cost).get("eur"), int | float
    ):
        return f"{eur:.4f}"
    return "—"
