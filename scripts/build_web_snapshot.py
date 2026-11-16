"""生成前端离线快照 apps/web/snapshot.js

快照内容全部从真实产物派生，用于内网静态托管（无后端进程）时仍可完整浏览系统。
本脚本不写入任何策略数值常量；读数一律读 output/result.json、output/equity.csv。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantlab.fingerprint import metric_fingerprint  # noqa: E402


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


def main() -> int:
    rep = json.loads((ROOT / "output" / "result.json").read_text(encoding="utf-8"))
    fp = metric_fingerprint(rep)
    snap = {
        "fingerprint": fp,
        "generated_at": rep.get("meta", {}).get("generated_at"),
        "result": rep,
        "equity": equity_series(),
        "opinions": parse_audit_panel(),
    }
    assert snap["equity"], "净值序列不能为空"
    assert snap["opinions"]["opinions"], "审核意见解析结果不能为空（防正则假阴性）"
    assert snap["result"]["quality_gate"]["final_verdict"], "质量门读数缺失"

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
          f"equity_points={len(snap['equity'])}  opinions={len(snap['opinions']['opinions'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
