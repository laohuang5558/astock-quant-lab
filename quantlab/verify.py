"""交付前一致性校验：数值指纹绑定 + 陈旧值扫描 + 自动渲染基准核对。

对应专家团审核 followup #10。三层检查：
  1) HTML 报告 / docs/metrics.md 的 metric-fingerprint 必须与当前 result.json 一致；
  2) docs/metrics.md 中每个 [[m:path]] 标记的读数必须与 result.json 逐项一致；
  3) 非历史对照类文档中不得出现 v1 已作废的关键数值（防口径漂移）。
审核报告与审核面板是对照材料，合法引用 v1 数值，故只校验指纹、不参与陈旧值扫描。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .fingerprint import METRIC_PATHS, metric_fingerprint, read_stamp, resolve

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent

DOC_FILES = ["README.md", "docs/ARCHITECTURE.md", "docs/STRATEGY_SPEC.md",
             "docs/DATA_CONTRACT.md", "docs/QUALITY_GATE.md", "docs/metrics.md"]
HTML_FILES = ["docs/DESIGN_REPORT.html", "docs/AUDIT_REPORT.html",
              "../astock-quant-design-report.html", "../astock-quant-audit-report.html"]
METRICS_MD = ROOT / "docs" / "metrics.md"
V1_FILE = ROOT / "output" / "result_v1_pre_audit.json"

_MARK_RE = re.compile(r"\[\[m:([^\]]+)\]\]\s*`([^`]*)`")


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def render_metrics(rep: dict) -> Path:
    """生成 docs/metrics.md：每个关键读数带可机读标记，禁止手工编辑。"""
    fp = metric_fingerprint(rep)
    lines = [
        "# 交付数值基准（自动生成，请勿手工编辑）", "",
        f"metric-fingerprint: `{fp}`", "",
        f"来源：`output/result.json`，生成时间 {rep['meta'].get('generated_at', '—')}。",
        "运行 `python -m quantlab verify` 校验；数值变化后重新执行 `python -m quantlab run` 并重建报告。",
        "", "| 路径 | 读数 |", "|---|---|",
    ]
    for p in METRIC_PATHS:
        lines.append(f"| `{p}` | [[m:{p}]] `{_fmt(resolve(rep, p))}` |")
    lines += ["", "> 本文件由脚本渲染，任何手工改动都会在 verify 阶段被判定为漂移。", ""]
    METRICS_MD.parent.mkdir(parents=True, exist_ok=True)
    METRICS_MD.write_text("\n".join(lines), encoding="utf-8")
    return METRICS_MD


PCT_PATHS = {
    "performance.total_return", "performance.annualized_return", "performance.max_drawdown",
    "performance.cost_share_of_gross", "factor_research.composite_ic.positive_ratio",
    "overfit.suite.3_monte_carlo_deflated_sharpe.baseline_percentile",
}


STALE_PATHS = [
    "performance.total_return", "performance.annualized_return", "performance.sharpe",
    "performance.max_drawdown", "performance.calmar", "performance.kurtosis",
    "performance.cost_total_cny", "performance.cost_share_of_gross", "performance.n_trades",
    "factor_research.composite_ic.ic_mean", "factor_research.composite_ic.t_stat",
    "factor_research.multiple_testing.n_rejected",
    "factor_research.multiple_testing.n_attempted",
    "overfit.suite.3_monte_carlo_deflated_sharpe.baseline_percentile",
    "overfit.suite.3_monte_carlo_deflated_sharpe.random_sharpe_mean",
    "audit.selftest.tests_run", "meta.sample.bars",
]


def _current_allowed(rep: dict) -> set[str]:
    """当前 result.json 中**确实存在**的数值写法集合，用于剔除误报。

    参数网格、成本敏感性、iid t 对照列等都可能恰好等于某个历史基准值，
    或当前 MDD 会被格式化成与历史相同的百分比串——这些都属合法引用。
    """
    vals: list[float] = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            vals.append(float(x))
            return
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(rep)
    out: set[str] = set()
    for v in vals:
        out.update({f"{v:.4f}", f"{v:.6f}", f"{v * 100:.2f}%", f"{v:,.2f}"})
    return out


def _stale_tokens(historical: list[dict], current: dict) -> list[str]:
    """从历史结果的关键绩效路径中提取「已变化且当前不存在」的数值串。

    三重防误报：① 只扫关键绩效路径（不扫参数网格）；② 与当前同路径值相同即跳过；
    ③ 该数值串若在当前结果任意位置合法存在（含百分比/千分位写法）即剔除。
    审核对照材料（AUDIT_*）不参与扫描，它们合法引用已作废的 v1 读数。
    """
    allowed = _current_allowed(current)
    tokens: list[str] = []
    for old_rep in historical:
        for p in STALE_PATHS:
            old, cur = resolve(old_rep, p), resolve(current, p)
            if not isinstance(old, (int, float)) or isinstance(old, bool) or old == cur:
                continue
            v = float(old)
            tokens += [f"{v:.4f}", f"{v:.6f}"]
            if p in PCT_PATHS:
                tokens.append(f"{v * 100:.2f}%")
            if p == "performance.cost_total_cny":
                tokens += [f"{v:.2f}", f"{v:,.2f}"]
    # 人工登记的 v1 隐性杠杆读数与旧单测数（原始 CSV 42,093 行是合法口径，不登记）
    tokens += ["−¥507,193.99", "507,193.99", "-507193.99",
               "11 项单测", "11 项自检"]
    out = []
    for t in tokens:
        t = t.strip()
        if len(t) >= 4 and t not in out and t not in allowed:
            out.append(t)
    return out


def verify(render: bool = False) -> dict:
    rp = ROOT / "output" / "result.json"
    issues: list[str] = []
    warnings: list[str] = []
    if not rp.exists():
        return {"ok": False, "issues": ["缺少 output/result.json，请先运行 python -m quantlab run"],
                "warnings": [], "fingerprint": None, "checked": []}
    rep = json.loads(rp.read_text(encoding="utf-8"))
    fp = metric_fingerprint(rep)

    if render:
        render_metrics(rep)

    checked: list[str] = []
    # 1) 指纹绑定
    for rel in HTML_FILES + ["docs/metrics.md"]:
        p = (ROOT / rel).resolve()
        checked.append(rel)
        if not p.exists():
            issues.append(f"缺少交付文件：{rel}")
            continue
        stamp = read_stamp(p) if p.suffix == ".html" else None
        if p.suffix == ".html":
            if stamp is None:
                issues.append(f"{rel} 缺少 metric-fingerprint（报告未随 result.json 重建）")
            elif stamp != fp:
                issues.append(f"{rel} 指纹 {stamp} != 当前 {fp}（报告未随 result.json 重建）")
        else:
            m = re.search(r"metric-fingerprint:\s*`([^`]+)`", p.read_text(encoding="utf-8"))
            if not m or m.group(1) != fp:
                issues.append(f"{rel} 指纹不匹配或缺失（当前应为 {fp}）")

    # 2) [[m:path]] 标记逐项核对
    if METRICS_MD.exists():
        text = METRICS_MD.read_text(encoding="utf-8")
        marks = _MARK_RE.findall(text)
        if len(marks) < len(METRIC_PATHS) - 2:
            issues.append(f"docs/metrics.md 标记数 {len(marks)} 少于关键指标数 {len(METRIC_PATHS)}")
        for path, shown in marks:
            actual = resolve(rep, path)
            if shown == "—":
                if actual is not None:
                    issues.append(f"docs/metrics.md {path} 记为 — 但实际为 {actual}")
                continue
            if actual is None:
                issues.append(f"docs/metrics.md {path} 记为 {shown} 但 result.json 为 None")
                continue
            if isinstance(actual, bool):
                if shown not in ("true", "false") or shown != ("true" if actual else "false"):
                    issues.append(f"docs/metrics.md {path} 值 {shown} != {actual}")
            elif isinstance(actual, (int, float)):
                try:
                    if abs(float(shown) - float(actual)) > 1e-9:
                        issues.append(f"docs/metrics.md {path} 值 {shown} != {actual}")
                except ValueError:
                    issues.append(f"docs/metrics.md {path} 值 {shown} 非数字")
            elif shown != str(actual):
                issues.append(f"docs/metrics.md {path} 值 {shown} != {actual}")
    else:
        issues.append("缺少 docs/metrics.md，请运行 python -m quantlab verify --render")

    # 3) 陈旧值扫描（审核对照材料豁免）
    historical = []
    for fname in ("result_v1_pre_audit.json", "result_v2_post_audit.json"):
        hp = ROOT / "output" / fname
        if hp.exists():
            historical.append(json.loads(hp.read_text(encoding="utf-8")))
    def has_token(token: str, text: str) -> bool:
        # 数值边界：避免把 -0.1806 里的 "0.1806" 或 0.34215 里的 "0.3421"
        # 误判为陈旧值；报告里的净值/回撤序列常含这类合法数字。
        return re.search(r"(?<![0-9.\-])" + re.escape(token) + r"(?![0-9.])", text) is not None

    if historical:
        tokens = _stale_tokens(historical, rep)
        scan_targets = DOC_FILES + HTML_FILES[:1] + [HTML_FILES[2]]
        for rel in scan_targets:
            p = (ROOT / rel).resolve()
            if not p.exists() or rel == "docs/metrics.md":
                continue
            txt = p.read_text(encoding="utf-8")
            for t in tokens:
                if has_token(t, txt):
                    issues.append(f"{rel} 出现已作废数值 {t!r}（口径漂移）")
    else:
        warnings.append("未找到 v1 审核基线，跳过陈旧值扫描")

    out = {
        "ok": len(issues) == 0,
        "fingerprint": fp,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "checked_files": checked,
        "metric_paths": len(METRIC_PATHS),
        "issues": issues,
        "warnings": warnings,
    }
    vp = ROOT / "output" / "verify.json"
    vp.parent.mkdir(parents=True, exist_ok=True)
    vp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
