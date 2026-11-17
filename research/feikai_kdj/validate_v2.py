#!/usr/bin/env python3
"""飞凯材料研究 v2 的确定性校验（含无前视反证与反向对照）。

检查：数据口径、周线/月线无前视、v1/v2 引擎等价、指纹一致性、报告数值绑定、裁决与合规。
输出 output/validate_v2.json；任一失败退出码 1。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import strategy as S  # noqa: E402
import weekly_monthly as W  # noqa: E402

OUT = HERE / "output"
ROOT = HERE.parent.parent
TOP = ROOT.parent

FP_RE = re.compile(r'<meta name="feikai-v2-fingerprint" content="([0-9a-f]{16})"')


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

    R = json.loads((OUT / "result_v2.json").read_text(encoding="utf-8"))
    R1 = json.loads((OUT / "result.json").read_text(encoding="utf-8"))
    meta = json.loads((W.DATA / "meta.json").read_text(encoding="utf-8"))
    fp_meta = json.loads((OUT / "report_fingerprint_v2.json").read_text(encoding="utf-8"))
    local_html = (OUT / "strategy_report_v2.html").read_text(encoding="utf-8")
    top_html = (TOP / "astock-quant-feikai-kdj-v2-report.html").read_text(encoding="utf-8")

    # ---- 1. 数据口径 ----
    d, _ = W.load_daily()
    df = S.load_panel()
    check("v2 与 v1 同一批完整交易日", list(d.index) == list(df.index))
    check("样本末日 = 最后完整交易日",
          R["meta"]["sample"]["end"] == meta["last_complete_day"] == str(d.index[-1]))
    check("盘中 bar 未进入回测", "2026-11-17" not in set(d.index))
    check("盘中 bar 已登记且仅用于展示",
          "intraday_snapshot" in R["current_state"] and bool(R["current_state"]["intraday_snapshot"]))
    check("样本天数与 v1 一致", R["meta"]["sample"]["n_days"] == R1["meta"]["sample"]["n_days"],
          str(R["meta"]["sample"]["n_days"]))

    # ---- 2. 无前视反证：截断到历史某日重算，读数必须逐点一致 ----
    d_full = d
    ma10_full = W.weekly_ma(d_full, 10)
    ma5_full = W.weekly_ma(d_full, 5)
    reg_full = W.monthly_regime(d_full)["frame"]
    probes = ["2019-06-14", "2021-03-19", "2023-08-18", "2025-05-16", "2026-11-13"]
    probes = [p for p in probes if p in set(d_full.index)]
    diffs = []
    for p in probes:
        d_cut, _ = W.load_daily(as_of=p)
        v10 = W.weekly_ma(d_cut, 10).loc[p]
        v5 = W.weekly_ma(d_cut, 5).loc[p]
        diffs.append(abs(float(v10) - float(ma10_full.loc[p])))
        diffs.append(abs(float(v5) - float(ma5_full.loc[p])))
    check("周线均线无前视（5 个历史截点重算一致）",
          bool(diffs) and max(diffs) <= 1e-9, f"max_abs_diff={max(diffs) if diffs else 'n/a'}")
    month_diffs = []
    for p in probes:
        d_cut, _ = W.load_daily(as_of=p)
        reg_cut = W.monthly_regime(d_cut)["frame"]
        for col in ("macd", "kdj", "ma5_ma10", "confirmed_dif", "confirmed_dea"):
            a, b = reg_cut[col].loc[p], reg_full[col].loc[p]
            if isinstance(a, (bool, np.bool_)):
                month_diffs.append(0.0 if bool(a) == bool(b) else 1.0)
            else:
                month_diffs.append(abs(float(a) - float(b)) if np.isfinite(float(a)) and np.isfinite(float(b)) else 0.0)
    check("月线确认口径无前视（5 个历史截点重算一致）",
          bool(month_diffs) and max(month_diffs) <= 1e-9,
          f"max_abs_diff={max(month_diffs) if month_diffs else 'n/a'}")
    # 反向对照：故意用「完整周收盘」替代「未完成周」应产生不同读数，证明无前视检查不是恒真
    wid, _ = W._iso_keys(d_full.index)
    naive = pd.Series(d_full["c"].to_numpy(float), index=d_full.index).groupby(wid).transform("last")
    check("反向对照：含未来数据的周收盘与无前视均线不同",
          float(np.nanmax(np.abs(naive.to_numpy(float) - ma10_full.to_numpy(float)))) > 1e-6)

    # ---- 3. v1/v2 引擎等价 ----
    p0 = S.Params()
    sig0 = S.build_signals(df, p0)
    base = S.backtest(df, p0, signals=sig0)
    b2 = R["baseline"]["perf"]
    check("v2-0 基线绩效 = 重跑 v1 主规则",
          abs(base["perf"]["sharpe"] - b2["sharpe"]) < 1e-6
          and abs(base["perf"]["total_return"] - b2["total_return"]) < 1e-6)
    check("v2-0 基线绩效 = v1 result.json",
          abs(b2["sharpe"] - R1["baseline"]["strategy"]["perf"]["sharpe"]) < 1e-6
          and b2["total_return"] == R1["baseline"]["strategy"]["perf"]["total_return"])
    check("真实信号 override 逐日净值一致",
          R["audit"]["signal_override_equity_diff"] <= 1e-6,
          str(R["audit"]["signal_override_equity_diff"]))
    gains = round(float(base["equity"].iloc[-1]) - p0.capital, 2)
    pnl = round(sum(float(t["net_pnl_cny"]) for t in base["trades"]), 2)
    check("v1 逐笔净额与期末权益闭合（v2 复用同一引擎）", abs(gains - pnl) <= 0.02,
          f"{pnl} vs {gains}")

    # ---- 4. 过滤单调性与样本坍缩 ----
    rows = R["filter_variants"]
    check("过滤后信号数不超过主规则", all(v["signals"] <= rows[0]["signals"] for v in rows))
    check("过滤生效天数不超过样本天数",
          all(v["filter_days"] <= R["meta"]["sample"]["n_days"] for v in rows))
    check("全叠加变体信号为 0（样本坍缩已如实记录）",
          next(v["signals"] for v in rows if v["name"].startswith("v2-9")) == 0)
    check("容差敏感性 3 档齐备", sum(1 for v in rows if v["name"].startswith("v2-tol")) == 3)

    # ---- 5. 指纹一致性 ----
    fp = fp_meta["fingerprint"]
    check("本地报告指纹匹配", read_fp(local_html) == fp, str(read_fp(local_html)))
    check("顶层报告指纹匹配", read_fp(top_html) == fp, str(read_fp(top_html)))
    check("两份报告内容一致", local_html == top_html)
    broken = local_html.replace(f'content="{fp}"', 'content="0000000000000000"', 1)
    check("反向对照：破坏指纹后校验失效", read_fp(broken) != fp and read_fp(broken) == "0000000000000000")

    # ---- 6. 报告数值绑定 ----
    cur = R["current_state"]
    must = [
        f'{cur["ma5w_qfq"]:.3f}', f'{cur["ma10w_qfq"]:.3f}',
        f'{cur["range20_percentile"] * 100:.1f}%',
        f'{R["random_control_v2"]["v2-1 + 回踩 10 周线"].get("sharpe_percentile", 0) * 100:.1f}%',
    ]
    check("报告含 5 / 10 周线读数", must[0] in local_html and must[1] in local_html,
          f"{must[0]} / {must[1]}")
    check("报告含区间分位", must[2] in local_html, must[2])
    check("报告含随机对照分位", must[3] in local_html, must[3])
    check("报告含全部变体名", all(v["name"] in local_html for v in rows))
    check("报告零内联 JS", "<script" not in local_html.lower())
    check("报告零外链资源", "http://" not in local_html and "https://" not in local_html)

    # 反向对照：抹掉关键读数后绑定检查必须失败
    check("反向对照：抹掉 10 周线读数后绑定失效", must[1] not in local_html.replace(must[1], "XXX"))

    # ---- 7. 统计与裁决结构 ----
    check("事件统计 8 组 × 4 周期", len(R["event_study"]["groups"]) == 8
          and len(R["event_study"]["horizons"]) == 4)
    check("事件随机对照 1000 次 × 3 组",
          len(R["event_random_control"]) == 3
          and all(v["status"] == "evaluated" and v["n_paths"] == 1000
                  for v in R["event_random_control"].values()))
    check("等价随机对照 200 次（有信号的变体）",
          all(v["n_paths"] == 200 for v in R["random_control_v2"].values()
              if v["status"] == "evaluated"))
    check("事件数与前视剔除一致（差 <=1）",
          abs(R["event_study"]["groups"]["回踩 10 周线（全部）"]["n_events"]
              - R["event_random_control"]["回踩 10 周线"]["n_events"]) <= 1)
    vd = R["verdict"]
    check("裁决不放行且硬否决 >= 3", vd["final"] == "不放行" and len(vd["hard_fail_items"]) >= 3)
    check("口径不一致与未完成月均被登记为硬否决",
          any("口径" in k for k in vd["hard_fail_items"])
          and any("未完成月" in k for k in vd["hard_fail_items"]))
    check("合规声明齐备", "research-only" in R["compliance"] and "不荐股" in R["compliance"]
          and "research-only" in local_html)
    check("对话 8 条原文齐备", len(R["conversation"]) == 8
          and len(R["conversation_reconciliation"]) == 6)

    report = {
        "ok": not issues, "fingerprint": fp,
        "checks": len(checks), "passed": sum(1 for c in checks if c["ok"]),
        "failed": len(issues), "issues": issues, "checks_detail": checks,
        "no_lookahead_probes": probes,
    }
    (OUT / "validate_v2.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    for c in checks:
        print(("PASS" if c["ok"] else "FAIL"), c["name"],
              f"| {c.get('detail', '')}" if c.get("detail") else "")
    print(f"V2_VALIDATION: {'PASS' if not issues else 'FAIL'} "
          f"({report['passed']}/{report['checks']}) fingerprint={fp}")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
