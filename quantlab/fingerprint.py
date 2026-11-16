"""交付数值指纹：把 result.json 的关键读数绑定成可校验的 hash。

用途：HTML 报告与 docs/metrics.md 在生成时写入同一指纹；verify 重算当前
result.json 的指纹，不一致即判定「报告未随结果重建」，杜绝手工转录与陈旧数值。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

METRIC_PATHS = [
    "meta.sample.dates", "meta.sample.codes", "meta.sample.bars",
    "meta.sample.start", "meta.sample.end",
    "performance.total_return", "performance.annualized_return", "performance.sharpe",
    "performance.max_drawdown", "performance.calmar", "performance.kurtosis",
    "performance.cost_total_cny", "performance.cost_share_of_gross",
    "performance.cash_min_cny", "performance.max_position_exposure",
    "performance.n_trades", "performance.blocked_orders", "performance.unfilled_orders",
    "factor_research.composite_ic.ic_mean", "factor_research.composite_ic.t_stat",
    "factor_research.composite_ic.positive_ratio",
    "factor_research.multiple_testing.n_tests",
    "factor_research.multiple_testing.n_attempted",
    "factor_research.multiple_testing.n_rejected",
    "overfit.suite.1_out_of_sample.oos_use_count",
    "overfit.suite.2_parameter_sensitivity.n_combos",
    "overfit.suite.2_parameter_sensitivity.consensus_sharpe",
    "overfit.suite.3_monte_carlo_deflated_sharpe.n_paths",
    "overfit.suite.3_monte_carlo_deflated_sharpe.baseline_percentile",
    "overfit.suite.3_monte_carlo_deflated_sharpe.control_equivalence",
    "risk.beta.beta", "risk.capacity.status",
    "audit.selftest.tests_run", "audit.oos_state.oos_use_count",
    "quality_gate.final_verdict", "quality_gate.counts.成立",
    "quality_gate.counts.不成立", "quality_gate.counts.存疑",
]

_STAMP_RE = re.compile(r'<meta\s+name="metric-fingerprint"\s+content="([^"]*)"\s*/?>', re.I)


def resolve(rep: dict, path: str):
    node = rep
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def metric_fingerprint(rep: dict) -> str:
    subset = {p: resolve(rep, p) for p in METRIC_PATHS}
    blob = json.dumps(subset, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def stamp_html(path: str | Path, fingerprint: str) -> None:
    """在 HTML 的 <head> 内写入/更新指纹 meta；已是最新则不改动文件。"""
    p = Path(path)
    if not p.exists():
        return
    s = p.read_text(encoding="utf-8")
    tag = f'<meta name="metric-fingerprint" content="{fingerprint}">'
    m = _STAMP_RE.search(s)
    if m:
        if m.group(1) == fingerprint:
            return
        s = s[:m.start()] + tag + s[m.end():]
    elif re.search(r"<head[^>]*>", s, re.I):
        s = re.sub(r"(<head[^>]*>)", r"\1\n  " + tag, s, count=1, flags=re.I)
    else:
        s = tag + "\n" + s
    p.write_text(s, encoding="utf-8")


def read_stamp(path: str | Path) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    m = _STAMP_RE.search(p.read_text(encoding="utf-8"))
    return m.group(1) if m else None
