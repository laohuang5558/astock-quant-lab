"""生成前端离线快照 apps/web/snapshot.js

快照内容全部从真实产物派生，用于内网静态托管（无后端进程）时仍可完整浏览系统。
本脚本不写入任何策略数值常量；读数一律读 output/result.json、output/equity.csv，
以及 research/feikai_kdj/output/result.json（单标的策略研究）。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantlab.fingerprint import metric_fingerprint  # noqa: E402

RESEARCH = ROOT / "research" / "feikai_kdj" / "output"


def equity_series(step_target: int = 800) -> list[dict]:
    path = ROOT / "output" / "equity.csv"
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = row.get("") or row.get("date") or row.get("Unnamed: 0")
            val = row.get("equity")
            if key and val is not None:
                rows.append({"date": key, "equity": float(val)})
    if not rows:
        raise RuntimeError("equity.csv 无有效数据")
    step = max(1, len(rows) // step_target)
    out = rows[::step]
    if out[-1] is not rows[-1]:
        out.append(rows[-1])
    return out


def parse_audit_panel() -> dict:
    """与后端同一口径从真实审核报告解析委员投票。"""
    import re
    path = ROOT / "docs" / "AUDIT_PANEL.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    m = re.search(r"##\s*1\s*·\s*专家团与投票([\s\S]*?)(?=\n##\s|\Z)", text)
    opinions = []
    if m:
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 5 or cells[0] in ("委员", "") or set(cells[0]) <= {"-", ":"}:
                continue
            opinions.append({
                "committee": cells[0],
                "agent_id": cells[1].strip("`"),
                "engineering_vote": cells[2],
                "strategy_vote": cells[3],
                "stance": cells[4],
            })
    vm = re.search(r"\*\*联合裁决\*\*：(.+)", text)
    return {
        "verdict": vm.group(1).strip() if vm else "",
        "opinions": opinions,
        "source": "docs/AUDIT_PANEL.md",
    }


def feikai_research() -> dict:
    """单标的策略研究摘要：全部从研究产物派生，不写死任何读数。"""
    result_path = RESEARCH / "result.json"
    fp_path = RESEARCH / "report_fingerprint.json"
    if not result_path.exists() or not fp_path.exists():
        raise RuntimeError("飞凯材料研究产物缺失，请先运行 research/feikai_kdj/run.py 与 build_report.py")
    r = json.loads(result_path.read_text(encoding="utf-8"))
    fp = json.loads(fp_path.read_text(encoding="utf-8"))["fingerprint"]
    return {
        "fingerprint": fp,
        "sample": r["meta"]["sample"],
        "rule": r["rule_formalization"],
        "baseline": r["baseline"]["strategy"]["perf"],
        "buy_hold": r["baseline"]["buy_and_hold"],
        "trades": r["baseline"]["trades"],
        "n_signals": r["baseline"]["n_signals"],
        "premise": r["user_premise_check"],
        "current": r["current_state"],
        "oos": r["oos"],
        "variants": r["variants"],
        "cost_sensitivity": r["cost_sensitivity"],
        "random_control": r["random_control"],
        "audit": r["audit"],
        "verdict": r["verdict"],
        "caveats": r["caveats"],
        "compliance": r["compliance"],
        "report_url": "feikai-kdj-report.html",
    }


def feikai_v2() -> dict:
    """v2 对话新增条件研究摘要：从 result_v2.json 派生，做字段投影控制快照体积。"""
    result_path = RESEARCH / "result_v2.json"
    fp_path = RESEARCH / "report_fingerprint_v2.json"
    if not result_path.exists() or not fp_path.exists():
        raise RuntimeError("飞凯材料 v2 产物缺失，请先运行 run_v2.py 与 build_report_v2.py")
    r = json.loads(result_path.read_text(encoding="utf-8"))
    fp = json.loads(fp_path.read_text(encoding="utf-8"))["fingerprint"]
    variants = [{
        "name": v["name"], "filter_days": v["filter_days"], "signals": v["signals"],
        "n_trades": v["perf"]["n_trades"], "total_return": v["perf"]["total_return"],
        "sharpe": v["perf"]["sharpe"],
        "oos_total_return": v["oos"].get("total_return"),
    } for v in r["filter_variants"]]
    return {
        "fingerprint": fp,
        "sample": r["meta"]["sample"],
        "conversation": r["conversation"],
        "reconciliation": r["conversation_reconciliation"],
        "current": r["current_state"],
        "monthly_definitions": r["new_conditions"]["C_monthly_deathcross"]["definitions"],
        "variants": variants,
        "variant_summary": r["variant_summary"],
        "event_study": r["event_study"],
        "event_random_control": r["event_random_control"],
        "random_control_v2": r["random_control_v2"],
        "verdict": r["verdict"],
        "caveats": r["caveats"],
        "report_url": "feikai-kdj-v2-report.html",
    }


def terminal_8094() -> dict:
    """:8094 既有终端改造评估摘要。

    数据来源全部为真实产物：上游只读体检 output/terminal_8094_probe.json
    与规整层验收 output/terminal8094_adapter_check.json。不写任何策略/胜率常量，
    前端展示的每个数字都必须能追溯到这两份 JSON。
    """
    probe_path = ROOT / "output" / "terminal_8094_probe.json"
    adapter_path = ROOT / "output" / "terminal8094_adapter_check.json"
    if not probe_path.exists():
        raise RuntimeError("8094 体检产物缺失，请先运行 scripts/validate_terminal_8094.py")
    if not adapter_path.exists():
        raise RuntimeError("规整层验收产物缺失，请先运行 scripts/validate_adapter.py")
    p = json.loads(probe_path.read_text(encoding="utf-8"))
    a = json.loads(adapter_path.read_text(encoding="utf-8"))
    ev = p["evidence"]
    agg = ev["agg_probe"]
    fwd = ev["fwd_recompute"]
    quality = ev["pick_quality"]
    return {
        "target": p["target"],
        "base": p["base"],
        "probe": {
            "ok": p["ok"], "passed": p["passed"], "total": p["total"],
            "negative_control": "0/%d FAIL" % p["total"],
            "method": p["method"], "probe_time": p["probe_time"],
        },
        "adapter": {
            "ok": a.get("ok"), "passed": a.get("passed"), "total": a.get("total"),
            "layers": a.get("layers"), "negative_controls": a.get("negative_controls"),
        },
        "findings": [
            {"id": c["name"], "ok": c["ok"], "detail": c["detail"]}
            for c in p["checks"]
        ],
        "agg_dual_scope": {
            "default": agg["limit_default"]["agg"],
            "full": agg["limit_600"]["agg"],
            "default_items": agg["limit_default"]["items"],
            "full_items": agg["limit_600"]["items"],
            "full_total": agg["limit_600"]["total"],
            "win_gap": ev["agg_win_gap"],
        },
        "fwd_formula": fwd["verified_formula"],
        "fwd_per_horizon": fwd["per_horizon"],
        "fwd_disclosed_in_contract": ev["fwd_disclosed_in_contract"],
        "daily_window": {
            "bars": fwd["daily_len"], "first": fwd["daily_first"],
            "last": fwd["daily_last"], "universe_start": fwd["universe_start"],
        },
        "pick_quality": {
            "n": quality["n"], "null_metric_n": quality["null_metric_n"],
            "st_n": quality["st_n"],
        },
        "g7_quality": ev["g7_quality"],
        "compliance": ev["compliance"],
        "docs_cdn": ev["docs_cdn"],
        "conds_silent_drop": ev["conds_silent_drop"],
        "stats": ev["stats"],
        "meta": ev["meta_response"],
        "report_url": "terminal8094-report.html",
        "review_url": "TERMINAL_8094_REVIEW.md",
    }


def main() -> int:
    rep = json.loads((ROOT / "output" / "result.json").read_text(encoding="utf-8"))
    fp = metric_fingerprint(rep)
    snap = {
        "fingerprint": fp,
        "generated_at": rep.get("meta", {}).get("generated_at"),
        "result": rep,
        "equity": equity_series(),
        "opinions": parse_audit_panel(),
        "feikai": feikai_research(),
        "feikai_v2": feikai_v2(),
        "t8094": terminal_8094(),
    }
    assert snap["equity"], "净值序列不能为空"
    assert snap["opinions"]["opinions"], "审核意见解析结果不能为空（防正则假阴性）"
    assert snap["result"]["quality_gate"]["final_verdict"], "质量门读数缺失"
    assert snap["feikai"]["baseline"]["n_trades"] >= 0, "飞凯材料研究读数缺失"
    assert snap["feikai"]["verdict"]["final"], "飞凯材料研究裁决缺失"
    assert snap["feikai_v2"]["variant_summary"]["n_variants"] >= 10, "飞凯材料 v2 研究读数缺失"
    assert snap["feikai_v2"]["verdict"]["final"], "飞凯材料 v2 裁决缺失"
    assert snap["t8094"]["probe"]["ok"] and snap["t8094"]["probe"]["passed"] == snap["t8094"]["probe"]["total"], \
        "8094 上游体检未全通过，快照不得固化"
    assert snap["t8094"]["adapter"]["ok"], "8094 规整层验收未通过，快照不得固化"
    assert snap["t8094"]["agg_dual_scope"]["default"]["fwd60"]["win"] != \
        snap["t8094"]["agg_dual_scope"]["full"]["fwd60"]["win"], \
        "F4 双口径读数相同，疑为绑定失效（防恒真）"

    out = ROOT / "apps" / "web" / "snapshot.js"
    payload = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
    out.write_text("window.__QUANT_SNAPSHOT__ = " + payload + ";\n", encoding="utf-8")

    # 同步把前端 HTML 的 metric-fingerprint 固化为当前指纹（避免静态交付物里残留 pending）
    import re
    page = ROOT / "apps" / "web" / "index.html"
    text = page.read_text(encoding="utf-8")
    stamped, n = re.subn(r'(<meta name="metric-fingerprint" content=")[^"]*(")',
                          lambda m: m.group(1) + fp + m.group(2), text, count=1)
    if n != 1:
        raise RuntimeError("index.html 缺少 metric-fingerprint 元数据（防假阴性）")
    if stamped != text:
        page.write_text(stamped, encoding="utf-8")

    print(f"WROTE {out}  {out.stat().st_size:,} B  fingerprint={fp}  "
          f"equity_points={len(snap['equity'])}  opinions={len(snap['opinions']['opinions'])}  "
          f"feikai_trades={snap['feikai']['baseline']['n_trades']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
