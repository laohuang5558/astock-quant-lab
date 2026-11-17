#!/usr/bin/env python3
"""飞凯材料研究的确定性校验（含反向对照，证明校验器不是恒真）。

检查：数据口径、结果闭合、等价性、指纹一致性、报告数值绑定、合规声明。
输出 output/validate_research.json；任一失败退出码 1。
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import strategy as S  # noqa: E402

OUT = HERE / "output"
DATA = HERE / "data"
ROOT = HERE.parent.parent
TOP = ROOT.parent

FP_RE = re.compile(r'<meta name="feikai-fingerprint" content="([0-9a-f]{16})"')


def read_fp(text: str) -> str | None:
    m = FP_RE.search(text)
    return m.group(1) if m else None


def main() -> int:
    checks: list[dict] = []
    issues: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        ok = bool(cond)
        checks.append({"name": name, "ok": ok, **({"detail": detail} if detail else {})})
        if not ok:
            issues.append(name)

    R = json.loads((OUT / "result.json").read_text(encoding="utf-8"))
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    fp_meta = json.loads((OUT / "report_fingerprint.json").read_text(encoding="utf-8"))
    local_html = (OUT / "strategy_report.html").read_text(encoding="utf-8")
    top_html = (TOP / "astock-quant-feikai-kdj-report.html").read_text(encoding="utf-8")

    # ---- 1. 数据口径 ----
    q = pd.read_csv(DATA / "feikai_qfq.csv")
    r = pd.read_csv(DATA / "feikai_raw.csv")
    x = pd.read_csv(DATA / "index_sh000001.csv")
    check("三份行情各有 2000+ 根", all(len(d) > 2000 for d in (q, r, x)),
          f"qfq={len(q)} raw={len(r)} index={len(x)}")
    check("前复权不含盘中 bar", "2026-11-17" not in set(q["date"]),
          f"qfq 末日={q['date'].iloc[-1]}")
    check("盘中 bar 已登记", "2026-11-17" in meta["intraday_bars"])
    check("回测截到最后完整交易日", R["meta"]["sample"]["end"] == meta["last_complete_day"],
          R["meta"]["sample"]["end"])
    check("标的名称跨源校验通过", meta["name_check"] == {"sz300398": "飞凯材料", "sh000001": "上证指数"})
    check("OHLC 无非正值", bool((q[["open", "high", "low", "close"]] > 0).all().all()))
    check("OHLC 包含关系成立", bool((q["high"] >= q[["open", "close"]].max(axis=1)).all()
                                and (q["low"] <= q[["open", "close"]].min(axis=1)).all()))

    # ---- 2. 净值序列闭合 ----
    eq = pd.read_csv(OUT / "equity.csv")
    bh = pd.read_csv(OUT / "equity_buyhold.csv")
    b = R["baseline"]["strategy"]["perf"]
    capital = R["meta"]["capital_note"]
    check("净值行数 = 样本天数", len(eq) == R["meta"]["sample"]["n_days"], str(len(eq)))
    check("净值首日 = 初始资金", abs(float(eq["equity"].iloc[0]) - 1_000_000.0) < 0.02,
          str(eq["equity"].iloc[0]))
    check("净值末值 = 报告期末权益", abs(float(eq["equity"].iloc[-1]) - b["final_equity_cny"]) < 0.02)
    check("买入持有净值行数一致", len(bh) == len(eq))
    check("总收益 = 末值/初值-1",
          abs(float(eq["equity"].iloc[-1]) / float(eq["equity"].iloc[0]) - 1 - b["total_return"]) < 1e-6)

    # ---- 3. 逐笔账闭合（独立重算，不信任 result.json 里的自述）----
    with open(OUT / "trades.csv", newline="", encoding="utf-8") as f:
        trades = list(csv.DictReader(f))
    pnl_sum = round(sum(float(t["net_pnl_cny"]) for t in trades), 2)
    gain = round(float(eq["equity"].iloc[-1]) - 1_000_000.0, 2)
    check("逐笔净额合计 = 期末权益增益", abs(pnl_sum - gain) <= 0.02, f"{pnl_sum} vs {gain}")
    check("交易笔数与绩效一致", len(trades) == b["n_trades"], str(len(trades)))
    check("现金下限与敞口上限合规", b["cash_min_cny"] >= -1e-6 and b["max_position_exposure"] <= 1.0 + 1e-6)

    # ---- 4. 等价性（真跑，不只信 result.json）----
    df = S.load_panel()
    p0 = S.Params()
    sig = S.build_signals(df, p0)
    base = S.backtest(df, p0)
    same = S.backtest(df, p0, signals=sig)
    d = float(np.max(np.abs(same["equity"].to_numpy() - base["equity"].to_numpy())))
    check("真实信号 override 逐日净值一致", d <= 1e-6, f"max_abs_diff={d}")
    check("真实信号 override 成本一致",
          abs(same["cost_total_cny"] - base["cost_total_cny"]) <= 1e-6)
    check("报告主规则绩效 = 重跑绩效",
          abs(base["perf"]["sharpe"] - b["sharpe"]) < 1e-6
          and abs(base["perf"]["total_return"] - b["total_return"]) < 1e-6)

    # ---- 5. 指纹一致性 ----
    fp = fp_meta["fingerprint"]
    check("本地报告指纹匹配", read_fp(local_html) == fp, str(read_fp(local_html)))
    check("顶层报告指纹匹配", read_fp(top_html) == fp, str(read_fp(top_html)))
    check("两份报告内容一致", local_html == top_html)

    # 反向对照：破坏指纹后校验函数必须失败，证明不是恒真
    broken = local_html.replace(f'content="{fp}"', 'content="0000000000000000"', 1)
    check("反向对照：破坏指纹后校验失效", read_fp(broken) != fp and read_fp(broken) == "0000000000000000")

    # ---- 6. 报告数值绑定（关键读数必须出现在 HTML 中）----
    total_s = f"{b['total_return'] * 100:.2f}%"
    sharpe_s = f"{b['sharpe']:.4f}"
    oos_s = f"{R['oos']['out_of_sample']['total_return'] * 100:+.2f}%"
    check("报告含主规则总收益", total_s in local_html, total_s)
    check("报告含主规则夏普", sharpe_s in local_html, sharpe_s)
    check("报告含样本外收益", oos_s in local_html, oos_s)
    check("报告含随机对照分位", f"{R['random_control']['sharpe_percentile'] * 100:.1f}%" in local_html)
    check("报告含全部 7 笔交易日期", all(t["entry_date"] in local_html for t in trades))
    check("报告零内联 JS", "<script" not in local_html.lower())
    check("报告零外链资源", "http://" not in local_html and "https://" not in local_html)

    # ---- 7. 严谨性结构 ----
    check("变体数 >= 10", len(R["variants"]) >= 10, str(len(R["variants"])))
    check("成本档位 4 档", len(R["cost_sensitivity"]) == 4)
    check("随机对照 200 次", R["random_control"]["n_paths"] == 200)
    check("等价性审计为真", R["audit"]["control_equivalence"]["equivalent"] is True)
    check("账目闭合审计为真", R["audit"]["trade_pnl_check"]["closed"] is True)
    check("裁决由读数推导且不放行", R["verdict"]["final"] == "不放行"
          and len(R["verdict"]["hard_fail_items"]) >= 3)
    check("合规声明齐备", "research-only" in R["compliance"]
          and "不荐股" in R["compliance"] and "research-only" in local_html)

    report = {
        "ok": not issues,
        "fingerprint": fp,
        "checks": len(checks),
        "passed": sum(1 for c in checks if c["ok"]),
        "failed": len(issues),
        "issues": issues,
        "checks_detail": checks,
    }
    (OUT / "validate_research.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for c in checks:
        print(("PASS" if c["ok"] else "FAIL"), c["name"],
              f"| {c.get('detail', '')}" if c.get("detail") else "")
    print(f"RESEARCH_VALIDATION: {'PASS' if not issues else 'FAIL'} "
          f"({report['passed']}/{report['checks']}) fingerprint={fp}")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
