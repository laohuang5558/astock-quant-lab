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
    }
    assert snap["equity"], "净值序列不能为空"
    assert snap["opinions"]["opinions"], "审核意见解析结果不能为空（防正则假阴性）"
    assert snap["result"]["quality_gate"]["final_verdict"], "质量门读数缺失"
    assert snap["feikai"]["baseline"]["n_trades"] >= 0, "飞凯材料研究读数缺失"
    assert snap["feikai"]["verdict"]["final"], "飞凯材料研究裁决缺失"
    assert snap["feikai_v2"]["variant_summary"]["n_variants"] >= 10, "飞凯材料 v2 研究读数缺失"
    assert snap["feikai_v2"]["verdict"]["final"], "飞凯材料 v2 裁决缺失"

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
