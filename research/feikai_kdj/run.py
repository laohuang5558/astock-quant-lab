#!/usr/bin/env python3
"""飞凯材料 KDJ 择时研究：完整可复跑编排，输出 result.json / equity.csv / trades.csv。

运行：python research/feikai_kdj/run.py
前置：python research/feikai_kdj/fetch_data.py
"""
from __future__ import annotations

import csv
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import strategy as S  # noqa: E402

OUT = HERE / "output"
IS_END = "2023-12-29"
OOS_START = "2024-01-02"

VARIANTS: list[tuple[str, dict]] = [
    ("主规则：2负+20日新低+拐头", {}),
    ("「2负」解作单负", {"j_neg_consecutive": 1}),
    ("「2负」解作3连负", {"j_neg_consecutive": 3}),
    ("「2负」解作 J<-5", {"j_threshold": -5.0}),
    ("「2负」解作 J<-10", {"j_threshold": -10.0}),
    ("只看个股，不设大盘条件", {"use_index_filter": False}),
    ("大盘新低窗口 10 日", {"index_newlow_window": 10}),
    ("大盘新低窗口 60 日", {"index_newlow_window": 60}),
    ("大盘新低允许错位 3 日", {"signal_window": 3}),
    ("不要求 J 拐头", {"require_j_turn": False}),
    ("持有上限 5 日", {"exit_max_hold": 5}),
    ("持有上限 20 日", {"exit_max_hold": 20}),
    ("指数自低点反弹 1.5% 出场", {"exit_index_rebound": 0.015}),
    ("仅按持有期出场（不做 J 上穿 0）", {"exit_j_cross_zero": False,
                                    "exit_take_profit": 9.9, "exit_stop_loss": -9.9}),
]


def clean(o):
    """把 numpy 标量递归转成 JSON 可序列化的 Python 原生类型。"""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def equity_rows(res: dict) -> list[dict]:
    e = res["equity"]
    return [{"date": str(d), "equity": round(float(v), 2)} for d, v in e.items()]


def compact(res: dict) -> dict:
    return {
        "perf": clean(res["perf"]),
        "n_signals": res["n_signals"],
        "n_days": res["n_days"],
        "exposure_days": res["exposure_days"],
        "blocked_orders": len(res["blocked"]),
        "open_position": clean(res["open_position"]),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = json.loads((S.DATA / "meta.json").read_text(encoding="utf-8"))
    df = S.load_panel()
    p0 = S.Params()

    base = S.backtest(df, p0)
    bh = S.buy_and_hold(df, p0)
    sig = S.build_signals(df, p0)

    # ---- 确定性审计 ----
    same = S.backtest(df, p0, signals=sig)  # 用真实信号作 override，必须与基线逐日一致
    max_abs_diff = float(np.max(np.abs(same["equity"].to_numpy() - base["equity"].to_numpy())))
    cost_diff = abs(float(same["cost_total_cny"]) - float(base["cost_total_cny"]))
    control_equivalence = {
        "equivalent": bool(max_abs_diff <= 1e-6 and cost_diff <= 1e-6),
        "max_abs_equity_diff": round(max_abs_diff, 9),
        "cost_abs_diff": round(cost_diff, 9),
        "note": "以真实信号序列经 signals= 重跑，证明随机对照只替换了信号、未改变引擎/成本/退出规则。",
    }
    trade_pnl_sum = round(sum(float(t["net_pnl_cny"]) for t in base["trades"]), 2)
    equity_gain = round(float(base["equity"].iloc[-1]) - p0.capital, 2)
    trade_pnl_check = {
        "trade_pnl_sum_cny": trade_pnl_sum,
        "equity_gain_cny": equity_gain,
        "abs_diff": round(abs(trade_pnl_sum - equity_gain), 2),
        "closed": abs(trade_pnl_sum - equity_gain) <= 0.02,
        "note": "空仓期间无利息、无费用，逐笔净额合计必须严格等于期末权益减初始资金。",
    }

    # ---- 用户前提的客观检验 ----
    premise = S.index_newlow_study(df, window=20)

    # ---- 当前状态（事实读数，不构成操作建议）----
    last = str(df.index[-1])
    prev = str(df.index[-2])
    roll20 = df["ic"].rolling(20, min_periods=20).min()
    is_newlow20 = bool(df["ic"].loc[last] <= roll20.loc[last] + 1e-9)
    intraday = {}
    for fname, key in (("index_sh000001.csv", "index"), ("feikai_raw.csv", "stock")):
        t = pd.read_csv(S.DATA / fname).set_index("date").sort_index()
        if "2026-11-17" in t.index:
            r = t.loc["2026-11-17"]
            intraday[key] = {"date": "2026-11-17", "open": float(r["open"]),
                             "high": float(r["high"]), "low": float(r["low"]),
                             "close": float(r["close"]),
                             "note": "盘中未收盘快照，未进入回测"}
    current = {
        "last_complete_day": last,
        "stock_close_qfq": round(float(df["c"].loc[last]), 4),
        "kdj_j": round(float(df["j"].loc[last]), 4),
        "kdj_k": round(float(df["k"].loc[last]), 4),
        "kdj_d": round(float(df["d"].loc[last]), 4),
        "j_negative": bool(df["j"].loc[last] < 0),
        "j_prev_day": round(float(df["j"].loc[prev]), 4),
        "index_close": round(float(df["ic"].loc[last]), 4),
        "index_20d_newlow": is_newlow20,
        "rule_signal_today": bool(sig.loc[last]),
        "last_signal_day": (str(list(sig[sig].index)[-1]) if sig.any() else None),
        "intraday_snapshot": intraday,
        "note": "仅为历史/当日事实读数，用于说明规则当前是否触发，不构成任何买卖点或操作建议。",
    }

    # ---- 样本内外 ----
    oos = {
        "in_sample": S.slice_perf(base, "2018-01-02", IS_END, p0.capital),
        "out_of_sample": S.slice_perf(base, OOS_START, last, p0.capital),
        "buy_hold_in_sample": S.slice_perf(bh, "2018-01-02", IS_END, p0.capital),
        "buy_hold_out_of_sample": S.slice_perf(bh, OOS_START, last, p0.capital),
        "split": [IS_END, OOS_START],
        "oos_use_count": 1,
    }

    # ---- 变体网格 ----
    variants = []
    for label, overrides in VARIANTS:
        p = replace(p0, name=label, **overrides)
        r = S.backtest(df, p)
        variants.append({
            "name": label, "overrides": clean(overrides),
            **compact(r),
        })
    sharpes = [v["perf"]["sharpe"] for v in variants]
    n_trades_all = [v["perf"]["n_trades"] for v in variants]

    # ---- 成本敏感性 ----
    costs = []
    for m in (1.0, 1.5, 2.0, 3.0):
        p = replace(p0, name=f"cost_x{m}", cost_mult=m)
        r = S.backtest(df, p)
        costs.append({"cost_mult": m, **compact(r)})

    # ---- 等价随机对照（只对主规则，200 次完整引擎重跑）----
    rc = S.equivalent_random_control(df, p0, base, n_paths=200)

    # ---- 裁决：全部由读数推导，不手写结论 ----
    n_trades = base["perf"]["n_trades"]
    sig_ok = bool(rc.get("significantly_better_than_random")) if rc.get("status") == "evaluated" else False
    hard_fail = {
        "样本量不足（平仓交易 < 30 笔）": n_trades < 30,
        "未显著优于等价随机选时": not sig_ok,
        "多重检验风险（变体数 >= 10 且无独立验证段）": len(variants) >= 10,
        "成本 ×3 后收益归零": costs[-1]["perf"]["total_return"] <= 0,
        "等价随机对照等价性不成立": not control_equivalence["equivalent"],
        "逐笔净额与期末权益不闭合": not trade_pnl_check["closed"],
    }
    verdict = {
        "final": "不放行",
        "engineering": "可复跑、可审计",
        "hard_fail_items": [k for k, v in hard_fail.items() if v],
        "hard_fail": hard_fail,
        "basis": "由真实读数推导：交易笔数、等价随机对照显著性、变体数与成本 ×3 结果",
        "note": "工程侧可复跑不等同于策略结论成立；本结论仅针对历史样本内的统计检验。",
    }

    result = {
        "meta": {
            "title": "飞凯材料 KDJ 择时策略研究（research-only）",
            "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "data": meta,
            "sample": {"start": str(df.index[0]), "end": last, "n_days": len(df)},
            "kdj": {"n": 9, "m1": 3, "m2": 3, "initial": 50.0,
                    "rsv_when_zero_range": 0.0, "warmup_days": S.WARMUP},
            "symbol": {"code": "300398", "name": "飞凯材料", "board": "创业板",
                       "limit_rule": "2020-08-24 前 10%，之后 20%（公开规则假设）"},
            "capital_note": "¥1,000,000 为研究假设规模，非用户实盘资金；step0.capital 仍为 null。",
            "compliance": meta["compliance"],
        },
        "rule_formalization": {
            "user_words": "飞凯材料的策略 上证指数现在每次新低有反弹。新低买，反弹t 个股，看kdj，j值为负数，2负反弹",
            "A_market": "上证指数收盘创 N 日新低（主规则 N=20）",
            "B_stock": "飞凯材料 KDJ(9,3,3) 的 J 连续 M 日为负（主规则 M=2）且当日 J 较前日回升",
            "entry": "A&B 同日 -> T 收盘出信号，T+1 开盘买入；涨停/停牌则放弃该次买入",
            "exit": "J 上穿 0 / 止盈 +5% / 止损 -5% / 指数自入场低点反弹 1.5%（变体） / 持有上限 10 日；T 收盘判定，T+1 开盘卖出；跌停则顺延重试",
            "t_plus_1_note": "A 股 T+1 不允许当日买入当日卖出，故「反弹做 T」被形式化为隔日波段，未建模日内回转（需底仓/融券，不在 research-only 范围）。",
            "ambiguity": "「2负」可能是连续两日 J<0、J<-2、或二次探底；不猜单一解释，用变体网格覆盖并登记多重检验风险。",
            "price_basis": meta["price_basis"],
        },
        "baseline": {"strategy": compact(base), "buy_and_hold": clean(bh["perf"]),
                     "buy_hold_liquidation_value_cny": clean(bh["liquidation_value_cny"]),
                     "trades": clean(base["trades"]),
                     "blocked": clean(base["blocked"]),
                     "n_signals": base["n_signals"]},
        "audit": {"control_equivalence": clean(control_equivalence),
                  "trade_pnl_check": clean(trade_pnl_check)},
        "user_premise_check": premise,
        "current_state": current,
        "oos": clean(oos),
        "variants": variants,
        "variant_summary": {
            "n_variants": len(variants),
            "sharpe_min": round(float(min(sharpes)), 6),
            "sharpe_max": round(float(max(sharpes)), 6),
            "sharpe_spread": round(float(max(sharpes) - min(sharpes)), 6),
            "n_trades_min": int(min(n_trades_all)),
            "n_trades_max": int(max(n_trades_all)),
            "note": "单轴扰动；变体越多，挑选最优者的事后过拟合风险越高，未做独立验证段。",
        },
        "cost_sensitivity": costs,
        "random_control": clean(rc),
        "verdict": clean(verdict),
        "caveats": [
            "样本仅 1 只个股、7 笔平仓交易，统计功效极低，任何胜率/夏普都不可外推。",
            "策略绝大部分时间空仓，低波动与低回撤主要来自「不持仓」，不是选股或择时能力的证明。",
            "前复权价随后续除权除息变动，历史上看到的绝对价格与实时行情不同。",
            "成交额为近似值，未用于容量测算；未建模分红再投资、停复牌现金处理、冲击成本、整手取整。",
            "创业板涨跌幅按日期分段是公开规则假设，未逐日核对个股特殊状态（如风险警示、新股前 5 日）。",
            "买入被涨跌停阻挡时直接放弃，卖出被阻挡时顺延；两条路径在真实与随机信号中一致，但属建模简化。",
            "样本外仅切分一次，oos_use_count=1；参数网格已在同一段数据上反复试过，样本外不再干净。",
        ],
        "compliance": "research-only：不含实盘下单、券商接口、自动交易循环；不荐股、不给买卖点与目标价、不承诺收益、不代操作；历史统计不构成投资建议。",
    }

    (OUT / "result.json").write_text(json.dumps(clean(result), ensure_ascii=False, indent=2), encoding="utf-8")
    rows = equity_rows(base)
    with open(OUT / "equity.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "equity"])
        w.writeheader(); w.writerows(rows)
    bh_rows = equity_rows(bh)
    with open(OUT / "equity_buyhold.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "equity"])
        w.writeheader(); w.writerows(bh_rows)
    if base["trades"]:
        with open(OUT / "trades.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(base["trades"][0].keys()))
            w.writeheader(); w.writerows(clean(base["trades"]))

    print(f"WROTE result.json / equity.csv / equity_buyhold.csv / trades.csv")
    print("BASE   ", json.dumps(clean(base["perf"]), ensure_ascii=False))
    print("BUYHOLD", json.dumps(clean(bh["perf"]), ensure_ascii=False))
    print("OOS    ", json.dumps(clean(oos["out_of_sample"]), ensure_ascii=False))
    print("RANDOM ", json.dumps(clean({k: rc[k] for k in
          ("status", "n_paths", "random_sharpe_mean", "baseline_sharpe",
           "sharpe_percentile", "significantly_better_than_random") if k in rc}), ensure_ascii=False))
    print("VARIANT", json.dumps(clean(result["variant_summary"]), ensure_ascii=False))
    print("VERDICT", json.dumps(clean(verdict), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
