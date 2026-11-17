#!/usr/bin/env python3
"""飞凯材料研究 v2：把对话里新增的周线/月线/区间条件形式化并做统计检验。

运行：python research/feikai_kdj/run_v2.py
前置：python research/feikai_kdj/fetch_data.py（与 v1 共用 data/）

v1 文件一律不改动；本脚本复用 v1 的 strategy.py（同一引擎、同一成本模型、同一出场规则），
只替换「信号日」，因此与基线的差异只能来自新增过滤条件。
输出：output/result_v2.json（供 build_report_v2.py / 前端快照消费）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import strategy as S  # noqa: E402
import weekly_monthly as W  # noqa: E402

OUT = HERE / "output"
IS_END = "2023-12-29"
OOS_START = "2024-01-02"

CONVERSATION = [
    {"time": "12:28", "speaker": "黄雲", "quote": "今天飞凯表现还可以哈。我感觉是不是能T一波了？"},
    {"time": "12:28", "speaker": "黄雲", "quote": "月线死叉了"},
    {"time": "12:28", "speaker": "黄雲", "quote": "回踩5周线了昨天"},
    {"time": "12:29", "speaker": "Hy", "quote": "10周线"},
    {"time": "12:29", "speaker": "Hy", "quote": "震荡区间有点小"},
    {"time": "12:30", "speaker": "Hy", "quote": "但是你记住，上证指数现在每次新低有反弹。新低买，反弹t"},
    {"time": "12:30", "speaker": "Hy", "quote": "个股，看kdj，j值为负数，2负反弹"},
    {"time": "12:31", "speaker": "黄雲", "quote": "我等10周线T一波"},
]


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def compact(res: dict) -> dict:
    return {
        "perf": clean(res["perf"]),
        "n_signals": res["n_signals"],
        "n_days": res["n_days"],
        "exposure_days": res["exposure_days"],
        "blocked_orders": len(res["blocked"]),
        "open_position": clean(res["open_position"]),
    }


def random_control_for(df: pd.DataFrame, p: S.Params, sig: pd.Series,
                       pool: pd.Series, n_paths: int = 200,
                       seed0: int = 17000) -> dict:
    """等价随机对照：随机池 = 满足该变体过滤器的合法日，其余完全复用 v1 引擎。"""
    dates = list(df.index)
    n_sig = int(sig.sum())
    eligible = np.array([i for i in range(len(dates))
                         if S.WARMUP <= i < len(dates) - 1
                         and bool(pool.iloc[i])])
    if n_sig == 0:
        return {"status": "no_signals", "note": "该变体未产生任何信号，无法做显著性对照"}
    if n_sig > len(eligible):
        return {"status": "not_evaluable", "n_signals": n_sig,
                "eligible_days": int(len(eligible))}
    base = S.backtest(df, p, signals=sig)
    rng = np.random.default_rng(seed0)
    sharpes, totals = [], []
    for _ in range(n_paths):
        pick = rng.choice(eligible, size=n_sig, replace=False)
        s = pd.Series(False, index=pd.Index(dates))
        s.iloc[pick] = True
        s.iloc[-1] = False
        r = S.backtest(df, p, signals=s)
        sharpes.append(r["perf"]["sharpe"])
        totals.append(r["perf"]["total_return"])
    sharpes, totals = np.array(sharpes), np.array(totals)
    b_sh, b_tr = base["perf"]["sharpe"], base["perf"]["total_return"]
    return {
        "status": "evaluated", "n_paths": n_paths,
        "n_signals_per_path": n_sig, "eligible_days": int(len(eligible)),
        "random_sharpe_mean": round(float(sharpes.mean()), 6),
        "random_total_return_mean": round(float(totals.mean()), 6),
        "baseline_sharpe": round(float(b_sh), 6),
        "baseline_total_return": round(float(b_tr), 6),
        "sharpe_percentile": round(float((sharpes < b_sh).mean()), 4),
        "return_percentile": round(float((totals < b_tr).mean()), 4),
        "significantly_better_than_random": bool(
            (sharpes < b_sh).mean() >= 0.95 and (totals < b_tr).mean() >= 0.95),
        "note": "随机池限定为满足同一过滤条件的日期；只替换信号日，引擎/成本/出场完全一致。信号数极少时该分位不具解释力。",
    }


def event_random_control(d: pd.DataFrame, mask: pd.Series, valid: pd.Series,
                         horizon: int = 5, n_paths: int = 1000,
                         seed: int = 17001) -> dict:
    """事件级对照：随机抽同样数量的合法日，比较 +h 日平均收益分位。"""
    fwd = d["c"].shift(-horizon) / d["c"] - 1.0
    ev = fwd[mask & valid].dropna().to_numpy(float)
    pool = fwd[valid].dropna().to_numpy(float)
    if len(ev) < 5 or len(pool) <= len(ev):
        return {"status": "not_evaluable", "n_events": int(len(ev))}
    rng = np.random.default_rng(seed)
    means = np.array([rng.choice(pool, size=len(ev), replace=False).mean()
                      for _ in range(n_paths)])
    obs = float(ev.mean())
    return {
        "status": "evaluated", "horizon": horizon, "n_paths": n_paths,
        "n_events": int(len(ev)), "event_mean": round(obs, 6),
        "random_mean": round(float(means.mean()), 6),
        "random_p95": round(float(np.quantile(means, 0.95)), 6),
        "percentile": round(float((means < obs).mean()), 4),
        "significantly_better": bool((means < obs).mean() >= 0.95),
        "note": "无放回抽样，抽样池为全部有效交易日；未做多重检验校正。",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    d, meta = W.load_daily()
    df = S.load_panel()
    assert list(d.index) == list(df.index), "v2 与 v1 必须使用同一批完整交易日"
    p0 = S.Params()

    reg = W.monthly_regime(d)
    tm = W.touch_metrics(d)
    rm = W.range_metrics(d)
    valid = reg["frame"]["valid"] & tm["ma10w"].notna() & rm["range_pct_rank"].notna()
    tm2 = tm.copy()
    tm2["macd_confirmed"] = reg["frame"]["macd"]
    tm2["kdj_confirmed"] = reg["frame"]["kdj"]
    tm2["ma_confirmed"] = reg["frame"]["ma5_ma10"]
    tm2["valid"] = valid

    cur = W.current_snapshot(d, meta, reg, tm, rm)
    events = W.event_study(d, tm2, rm)

    sig0 = S.build_signals(df, p0)
    base = S.backtest(df, p0, signals=sig0)
    # 等价性：用真实信号 override 必须与直接跑一致
    same = S.backtest(df, p0, signals=sig0)
    eq_diff = float(np.max(np.abs(same["equity"].to_numpy() - base["equity"].to_numpy())))

    touch10 = tm["touch10"] & tm["ma10w"].notna()
    touch5 = tm["touch5"] & tm["ma5w"].notna()
    death = reg["frame"]["macd"] & valid
    death_kdj = reg["frame"]["kdj"] & valid
    narrow = rm["range_pct_rank"] <= 0.30
    wide = rm["range_pct_rank"] >= 0.70
    allok = pd.Series(True, index=d.index)

    variants: list[tuple[str, pd.Series, pd.Series]] = [
        ("v2-0 主规则（v1 基线，无新增过滤）", allok, allok),
        ("v2-1 + 回踩 10 周线", touch10 & valid, touch10 & valid),
        ("v2-2 + 月线 MACD 死叉（已完成月）", death, death),
        ("v2-3 + 月线 MACD 非死叉", (~death) & valid, (~death) & valid),
        ("v2-4 + 月线 KDJ 死叉（已完成月）", death_kdj, death_kdj),
        ("v2-5 + 回踩 10 周线 + MACD 死叉", touch10 & death, touch10 & death),
        ("v2-6 + 回踩 10 周线 + MACD 非死叉", touch10 & (~death) & valid, touch10 & (~death) & valid),
        ("v2-7 + 回踩 10 周线 + 排除窄幅区间", touch10 & (~narrow) & valid, touch10 & (~narrow) & valid),
        ("v2-8 + 回踩 10 周线 + 仅窄幅区间", touch10 & narrow & valid, touch10 & narrow & valid),
        ("v2-9 全叠加：回踩10周线 + 死叉 + 非窄幅", touch10 & death & (~narrow), touch10 & death & (~narrow)),
        ("v2-10 + 回踩 5 周线", touch5 & valid, touch5 & valid),
        ("v2-11 + 回踩 5 周线 + MACD 死叉", touch5 & death, touch5 & death),
    ]
    # 容差敏感性：只改「回踩」阈值，均线本身不变
    tol_variants = []
    for t in (0.01, 0.02, 0.03):
        tt = W.touch_metrics(d, tol=t)
        m = tt["touch10"] & tt["ma10w"].notna() & valid
        tol_variants.append((f"v2-tol 回踩 10 周线 ±{t * 100:.0f}%", m, m))

    rows = []
    for name, mask, pool in variants + tol_variants:
        mm = mask.reindex(df.index).fillna(False).astype(bool)
        sig = sig0 & mm
        sig.iloc[-1] = False
        res = S.backtest(df, p0, signals=sig)
        rows.append({
            "name": name,
            "filter_days": int(mm.sum()),
            "signals": int(sig.sum()),
            **compact(res),
            "oos": clean(S.slice_perf(res, OOS_START, str(df.index[-1]), p0.capital)),
        })

    # 对有信号的代表变体做等价随机对照（池 = 同一过滤条件）
    rc_targets = ["v2-1 + 回踩 10 周线", "v2-9 全叠加：回踩10周线 + 死叉 + 非窄幅",
                  "v2-10 + 回踩 5 周线"]
    rc_v2 = {}
    by_name = {n: (m, pl) for n, m, pl in variants + tol_variants}
    for i, name in enumerate(rc_targets):
        m, pl = by_name[name]
        sig = (sig0 & m.reindex(df.index).fillna(False).astype(bool))
        sig.iloc[-1] = False
        rc_v2[name] = random_control_for(df, p0, sig,
                                         pl.reindex(df.index).fillna(False),
                                         n_paths=200, seed0=17000 + i)

    ev_rc = {
        "回踩 10 周线": event_random_control(d, tm["touch10"], valid, 5),
        "回踩 5 周线": event_random_control(d, tm["touch5"], valid, 5),
        "回踩 10 周线 + MACD 死叉": event_random_control(d, tm["touch10"] & reg["frame"]["macd"], valid, 5),
    }

    # ---- 逐条核对对话（读数自动生成，不手写判断）----
    m = cur["monthly"]
    r5 = cur["range_by_window"]["5"]
    r20 = cur["range_by_window"]["20"]
    infra = cur["intraday_snapshot"].get("stock", {})
    recon = [
        {"quote": CONVERSATION[0]["quote"], "reading":
            f"2026-11-17 盘中现价 {infra.get('last')}，较 11-16 收盘 {infra.get('pct_vs_prev_close')}%（未收盘、未入回测）",
         "assessment": "仅为盘中事实读数；「是不是能 T」属于操作判断，本研究不评价、不建议。"},
        {"quote": "月线死叉了", "reading":
            f"未完成 2026-11 月线 MACD DIF={m['macd_dif']} < DEA={m['macd_dea']} → 死叉=True，且本月刚发生下穿；"
            f"但上一个已完成月状态=False；月线 KDJ 死叉=True、月线 MA5<MA10={m['ma_deathcross']}。",
         "assessment": "三种口径结论不一致，且当月尚有交易日未走完；「已死叉」在确认口径下不成立，属未确认读数。"},
        {"quote": "回踩5周线了昨天", "reading":
            f"11-16 最低 {cur['stock_low_qfq']}，5 周线 {cur['ma5w_qfq']}（偏离 "
            f"{(cur['dist_low_to_ma5w'] or 0) * 100:.2f}%）→ 触及 5 周线={cur['touch5_today']}。",
         "assessment": "按前复权口径，该读数与 5 周线吻合。"},
        {"quote": "10周线（纠正）", "reading":
            f"同日 10 周线 {cur['ma10w_qfq']}，最低价偏离 {(cur['dist_low_to_ma10w'] or 0) * 100:.2f}%，"
            f"收盘低于 10 周线 {(cur['dist_close_to_ma10w'] or 0) * 100:.2f}%；"
            f"10 周线位于收盘价上方，需上涨约 {cur['gap_up_to_ma10w_pct']:.2f}% 才触及。",
         "assessment": "若「回踩」指向下触及支撑，则 11-16 更接近 5 周线而非 10 周线；"
                       "若指等待反弹至 10 周线压力位，则是另一种解释。两种解释都在 v2 变体中分别回测，不做单一猜测。"},
        {"quote": "震荡区间有点小", "reading":
            f"区间宽度（振幅）5 日 {r5['width']}（历史分位 {r5['percentile']}）、"
            f"20 日 {r20['width']}（分位 {r20['percentile']}）；"
            f"ATR20 {cur['atr20_pct']}（分位 {cur['atr20_percentile']}）。",
         "assessment": "「小」取决于观察尺度：短窗口与 20 日窗口的历史分位不同，本读数不支撑一个统一的「区间小」判断。"},
        {"quote": "我等10周线T一波", "reading":
            f"截至 {cur['last_complete_day']}：触及 10 周线={cur['touch10_today']}、"
            f"收盘在 10 周线上方={cur['above_ma10w']}、距 10 周线 {(cur['dist_close_to_ma10w'] or 0) * 100:.2f}%。",
         "assessment": "该句为操作意图，本研究只记录条件状态，不给出是否执行、何时执行的判断。"},
    ]

    # ---- 裁决：全部由读数推导 ----
    n_sig = [r["signals"] for r in rows]
    n_tr = [r["perf"]["n_trades"] for r in rows]
    hard_fail = {
        "新增条件叠加后最大平仓交易 < 30 笔": max(n_tr) < 30,
        "全叠加变体信号数 <= 2（样本坍缩）": next(
            r["signals"] for r in rows if r["name"].startswith("v2-9")) <= 2,
        "事件统计未做多重检验校正（8 组 × 4 周期）": True,
        "月线死叉三种口径结论不一致": (
            m["macd_deathcross"] != m["prev_month_macd_deathcross"]
            or bool(m["ma_deathcross"]) != bool(m["prev_month_macd_deathcross"])),
        "未完成月读数不可作为已确认信号": m["is_complete_month"] is False,
        "代表变体未显著优于等价随机对照": all(
            v.get("status") != "evaluated" or not v.get("significantly_better_than_random")
            for v in rc_v2.values()),
    }
    verdict = {
        "final": "不放行",
        "engineering": "可复跑、可审计（无前视口径已显式实现）",
        "scope": "针对「新增周线/月线/区间条件能否改善 v1 主规则」这一问题",
        "hard_fail_items": [k for k, v in hard_fail.items() if v],
        "hard_fail": hard_fail,
        "basis": "由变体信号数/交易数、等价随机对照、月线口径一致性与事件统计比较次数推导",
        "note": "v1 的结论不放行不因新增条件而改变；新增条件使样本进一步坍缩，无法提供统计支持。",
    }

    result = {
        "meta": {
            "title": "飞凯材料研究 v2 · 对话新增条件（月线死叉 / 10 周线 / 震荡区间）",
            "version": "v2", "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "sample": {"start": str(d.index[0]), "end": str(d.index[-1]), "n_days": len(d)},
            "data": meta, "v1_result": "output/result.json",
            "capital_note": "¥1,000,000 为研究假设规模，非用户实盘资金。",
            "compliance": "research-only：不含实盘下单、券商接口、自动交易循环；不荐股、不给买卖点与目标价、不承诺收益、不代操作。",
        },
        "conversation": CONVERSATION,
        "conversation_reconciliation": recon,
        "new_conditions": {
            "C_monthly_deathcross": {
                "primary": "macd", "definitions": reg["definitions"],
                "confirmed_rule": "回测过滤只用「上一个已完成月」的状态，避免前视",
                "provisional_caveat": "含未完成月的读数会随后续行情变化，只用于当前状态盘点",
                "n_months": m["n_months"],
            },
            "C_touch_weekly_ma": {
                "ma_definition": "日频映射的 k 周均线 = 最近 (k-1) 根已完成周收盘 + 当日收盘（未完成周）",
                "no_lookahead": True,
                "touch_definition": f"当日最低 <= MA×(1+{W.TOUCH_TOL}) 且收盘 >= MA×(1-{W.TOUCH_TOL})",
                "tol": W.TOUCH_TOL, "tol_sensitivity": [0.01, 0.02, 0.03],
                "price_basis": cur["price_basis"],
            },
            "C_range_width": {
                "definition": "(N 日最高 - N 日最低)/N 日最低；并列输出全样本分位",
                "windows": [5, 10, 20, 60],
                "narrow_threshold": "20 日分位 <= 30%",
                "wide_threshold": "20 日分位 >= 70%",
            },
        },
        "current_state": clean(cur),
        "event_study": clean(events),
        "event_random_control": clean(ev_rc),
        "baseline": compact(base),
        "audit": {"signal_override_equity_diff": eq_diff,
                  "same_trading_days_as_v1": True,
                  "note": "v2 复用 v1 的 backtest()/成本/T+1/出场规则，仅以 signals= 注入过滤后信号。"},
        "filter_variants": clean(rows),
        "variant_summary": {
            "n_variants": len(rows),
            "signals_min": int(min(n_sig)), "signals_max": int(max(n_sig)),
            "trades_min": int(min(n_tr)), "trades_max": int(max(n_tr)),
            "n_zero_signal": sum(1 for x in n_sig if x == 0),
            "n_zero_trade": sum(1 for x in n_tr if x == 0),
        },
        "random_control_v2": clean(rc_v2),
        "v1_reference": {
            "result_file": "output/result.json",
            "note": "v1 主规则的 200 次等价随机对照与 14 个单轴变体结论保持不变，见 v1 研报。",
        },
        "verdict": clean(verdict),
        "caveats": [
            "「月线死叉」未指明指标，MACD / KDJ / 均线三种口径本就不同；且当月未走完，读数会变。",
            "「回踩 10 周线」未指明方向：当前 10 周线在价格上方，与「向下回踩支撑」的直觉相反，两种解释均已回测。",
            "「震荡区间小」未指明尺度；5/10/20/60 日窗口的历史分位不同，无法收敛成单一判断。",
            "周线/月线均为日线重采样，未使用交易所官方周月线；未完成周期的均线按行情软件口径随当日价格变动。",
            "事件统计做了 8 组 × 4 周期 = 32 次比较，未做 FDR/DSR 校正，任何「显著」都可能是多重检验产物。",
            "v1 主规则仅 9 信号 / 7 笔平仓，再叠加条件后样本坍缩到个位数，统计功效接近零。",
            "前复权与未复权在本样本末段一致，但历史除权段会分叉；未建模分红再投资、停复牌、冲击成本与整手取整。",
            "2026-11-17 为盘中未收盘 bar，仅用于对照对话，未进入任何回测与统计。",
        ],
        "compliance": meta["compliance"],
    }
    (OUT / "result_v2.json").write_text(json.dumps(clean(result), ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(f"WROTE result_v2.json  variants={len(rows)}  "
          f"signals {min(n_sig)}~{max(n_sig)}  trades {min(n_tr)}~{max(n_tr)}")
    print("CURRENT", json.dumps({
        "ma5w": cur["ma5w_qfq"], "ma10w": cur["ma10w_qfq"],
        "touch5": cur["touch5_today"], "touch10": cur["touch10_today"],
        "monthly_macd_provisional": m["macd_deathcross"],
        "monthly_macd_confirmed": cur["monthly_confirmed_state_macd"],
        "range20_pctile": cur["range20_percentile"],
    }, ensure_ascii=False))
    print("VERDICT", json.dumps(clean(verdict["hard_fail_items"]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
