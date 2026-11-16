"""分析层：绩效、样本外、参数敏感性、蒙特卡洛/Deflated Sharpe、成本敏感性、归因。

过拟合四件套缺一不可；任何一项无法计算就写 None + 原因，绝不编造。
"""
from __future__ import annotations

import math
from typing import Dict

import numpy as np
import pandas as pd

from .pipeline import run_backtest, run_backtest_with_random_weights
from .risk import max_drawdown_detail


def _perf_from_equity(eq: pd.Series) -> dict:
    eq = eq.dropna()
    if len(eq) < 2:
        return {"error": "净值点不足"}
    r = eq.pct_change().dropna()
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    n = max(1, len(r))
    ann = float((eq.iloc[-1] / eq.iloc[0]) ** (252.0 / n) - 1.0)
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    vol = sd * math.sqrt(252)
    sharpe = float(r.mean() / sd * math.sqrt(252)) if sd > 0 else None
    mdd = float(max_drawdown_detail(eq)["max_drawdown"])
    idx = pd.to_datetime(eq.index)
    monthly = pd.Series(eq.values, index=idx).resample("ME").last().pct_change().dropna()
    return {
        "start": str(eq.index[0]), "end": str(eq.index[-1]), "trading_days": int(len(eq)),
        "total_return": round(total, 6), "annualized_return": round(ann, 6),
        "annualized_vol": round(vol, 6),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown": round(mdd, 6),
        "calmar": round(ann / abs(mdd), 4) if mdd < 0 else None,
        "monthly_win_rate": round(float((monthly > 0).mean()), 4) if len(monthly) else None,
        "daily_sharpe": round(float(r.mean() / sd), 6) if sd > 0 else None,
        "skew": round(float(r.skew()), 4), "kurtosis": round(float(r.kurtosis()) + 3.0, 4),
    }


def performance(result, cfg) -> dict:
    p = _perf_from_equity(result.equity)
    net = float(result.equity.iloc[-1] - result.equity.iloc[0])
    gross = net + result.cost_total
    executed = int(result.trades["side"].isin(["BUY", "SELL"]).sum()) if len(result.trades) else 0
    ch = getattr(result, "cash_history", None)
    eh = getattr(result, "exposure_history", None)
    cash = ch.copy() if isinstance(ch, pd.Series) else pd.Series(dict(ch or {}))
    expo = eh.copy() if isinstance(eh, pd.Series) else pd.Series(dict(eh or {}))
    p.update({
        "cost_total_cny": round(result.cost_total, 2),
        "cost_breakdown_cny": result.cost_breakdown,
        "gross_profit_proxy_cny": round(gross, 2),
        "cost_share_of_gross": round(result.cost_total / gross, 6) if gross > 0 else None,
        "cost_share_note": "毛收益=净收益+全部成本；若毛收益<=0 则占比不适用" if gross <= 0 else "",
        "avg_turnover_per_rebalance": round(float(np.mean(list(result.turnover_history.values()))), 4)
        if result.turnover_history else None,
        "turnover_basis": "目标信号口径（非实际成交换手；被拒订单会造成偏离）",
        "blocked_orders": int(result.blocked_count),
        "n_trades": executed,
        "n_blocked_records": int(len(result.trades)) - executed,
        "cash_min_cny": round(float(cash.min()), 2) if len(cash) else None,
        "max_position_exposure": round(float(expo.max()), 6) if len(expo) else None,
        "unfilled_orders": len(getattr(result, "unfilled_history", {}) or {}),
    })
    return p


def oos_check(result, cfg) -> dict:
    split = cfg.get("period.oos_start", "2024-01-01")
    eq = result.equity
    ins = eq[eq.index < split]
    oos = eq[eq.index >= split]
    return {
        "split_date": split,
        "in_sample": _perf_from_equity(ins),
        "out_of_sample": _perf_from_equity(oos),
        "oos_use_count": int(cfg.get("validation.oos_use_count", 1)),
        "oos_max_uses": int(cfg.get("validation.oos_max_uses", 3)),
        "note": "计数由 state/research_state.json 持久化；策略参数指纹变化即自动递增，超上限由质量门降级",
    }


def sensitivity_combo_count(cfg) -> int:
    """真实参数试验次数：基准点 1 + 每个轴的非基准取值个数（单轴逐一扰动）。

    该数字由配置推导，不再由人工填写常量；FDR 尝试数分母与 DSR n_trials 共用它。
    """
    axes = cfg.get("validation.sensitivity_axes", {}) or {}
    n = 1
    for axis, vals in axes.items():
        cur = cfg.get(axis)
        n += len([v for v in vals if v != cur])
    return int(n)


def parameter_sensitivity(md, cfg) -> dict:
    """扩宽邻域网格：因子权重 / 择时 / 熔断 / 成本 / 持仓数 / 权重上限均单轴扰动。

    基准参数取「邻域共识」而非单点：以全部邻域点夏普中位数为共识读数，
    单点基准显著高于共识即记为「点估计过拟合嫌疑」。
    """
    axes = cfg.get("validation.sensitivity_axes", {}) or {}
    rows = []

    def one(label: str, overrides: dict, is_baseline: bool):
        c = cfg.with_overrides(**overrides) if overrides else cfg
        row = {"axis": label, "overrides": overrides, "is_baseline": is_baseline,
               "top_n": int(c.get("strategy.top_n", 8)),
               "ma_long": int(c.get("strategy.trend.ma_long", 120))}
        try:
            res = run_backtest(md, c)["result"]
            p = _perf_from_equity(res.equity)
            row.update({"annualized_return": p.get("annualized_return"),
                        "sharpe": p.get("sharpe"), "max_drawdown": p.get("max_drawdown")})
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)

    one("baseline", {}, True)
    for axis, vals in axes.items():
        cur = cfg.get(axis)
        for v in vals:
            if v == cur:
                continue
            one(axis, {axis: v}, False)

    sh = [r["sharpe"] for r in rows if r.get("sharpe") is not None]
    spread = round(max(sh) - min(sh), 4) if len(sh) >= 2 else None
    cliff = bool(spread is not None and (spread > 0.8 or (min(sh) < 0 < max(sh))))
    neigh = [r["sharpe"] for r in rows if not r.get("is_baseline") and r.get("sharpe") is not None]
    consensus = round(float(np.median(neigh)), 4) if neigh else None
    base_row = next((r for r in rows if r.get("is_baseline")), {})
    base_sh = base_row.get("sharpe")
    gap = round(float(base_sh - consensus), 4) if (base_sh is not None and consensus is not None) else None
    return {
        "grid": rows, "n_combos": len(rows), "axes": list(axes.keys()),
        "sharpe_spread": spread, "cliff_like_decay": cliff,
        "consensus_sharpe": consensus, "baseline_sharpe": base_sh,
        "point_minus_consensus": gap,
        "point_estimate_suspicion": bool(gap is not None and gap > 0.15),
        "note": "单轴逐一扰动（one-at-a-time），非全因子设计；共识=邻域点夏普中位数；"
                "单点高于共识 >0.15 记为点估计过拟合嫌疑",
    }


def _normal_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def deflated_sharpe(daily_sr: float, n_obs: int, n_trials: int, skew: float, kurt: float) -> dict:
    """Bailey & López de Prado 的近似实现（非年化 SR、独立试验假设）。"""
    if n_obs < 30 or daily_sr is None or n_trials < 1:
        return {"dsr": None, "note": "样本或试验数不足"}
    gamma = 0.5772156649
    z = math.sqrt(2.0 * math.log(n_trials))
    sr0 = (1.0 / math.sqrt(n_obs - 1)) * z * (1.0 - gamma / z) if z > 0 else 0.0
    denom = math.sqrt(max(1e-12, 1.0 - skew * daily_sr + (kurt - 1.0) / 4.0 * daily_sr ** 2))
    dsr = _normal_cdf((daily_sr - sr0) * math.sqrt(n_obs - 1) / denom)
    return {"dsr": round(float(dsr), 6), "expected_max_sr_under_null": round(float(sr0), 6),
            "n_trials": int(n_trials), "n_obs": int(n_obs),
            "note": "近似式；假设各试验独立，实际因子间相关会改变阈值，结论置信度中等"}


def monte_carlo(md, cfg, result, baseline: dict, score=None) -> dict:
    """等价随机对照：复用完整权重函数 + 两段式撮合，真跑 n_paths 次完整引擎。

    等价性不是自称，而是可证断言：用真实 score 走 score_override 路径重跑一次，
    其逐日净值与成本必须与基准逐值一致（差异 <=1e-6），否则 control_equivalence=False
    且分位数 / DSR 判为无效、不进入质量门结论。
    """
    from .factors import build_score
    from .pipeline import build_random_score, run_backtest

    if score is None:
        score, _ = build_score(md, cfg)
    n_paths = int(cfg.get("validation.mc_paths", 200))

    # ---- 等价性可证检查 ----
    check = run_backtest(md, cfg, score_override=score)["result"]
    same_len = len(check.equity) == len(result.equity)
    max_diff = (float(np.max(np.abs(check.equity.to_numpy() - result.equity.to_numpy())))
                if same_len and len(result.equity) else float("inf"))
    cost_diff = abs(float(check.cost_total) - float(result.cost_total))
    equivalent = bool(same_len and np.isfinite(max_diff) and max_diff <= 1e-6 and cost_diff <= 1e-6)

    sharpes, dd_list, cost_list = [], [], []
    for i in range(n_paths):
        rnd = build_random_score(score, seed=9000 + i)
        res = run_backtest(md, cfg, score_override=rnd)["result"]
        p = _perf_from_equity(res.equity)
        if p.get("sharpe") is not None:
            sharpes.append(float(p["sharpe"]))
        if p.get("max_drawdown") is not None:
            dd_list.append(float(p["max_drawdown"]))
        cost_list.append(float(res.cost_total))
    arr = np.array(sharpes, dtype=float)
    b = baseline.get("sharpe")
    raw_grid = cfg.get("validation.trials_param_grid", None)
    try:
        n_grid = int(raw_grid)
    except (TypeError, ValueError):
        n_grid = sensitivity_combo_count(cfg)
    trials = int(cfg.get("validation.trials_factor_count", 6)) * n_grid
    dsr = deflated_sharpe(baseline.get("daily_sharpe"), baseline.get("trading_days", 0), trials,
                          baseline.get("skew", 0.0), baseline.get("kurtosis", 3.0))
    percentile = round(float((arr < b).mean()), 4) if arr.size and b is not None else None
    return {
        "n_paths": int(len(arr)),
        "random_sharpe_mean": round(float(arr.mean()), 4) if arr.size else None,
        "random_sharpe_std": round(float(arr.std(ddof=1)), 4) if arr.size > 1 else None,
        "random_sharpe_p95": round(float(np.quantile(arr, 0.95)), 4) if arr.size else None,
        "random_mdd_median": round(float(np.median(dd_list)), 4) if dd_list else None,
        "random_cost_mean_cny": round(float(np.mean(cost_list)), 2) if cost_list else None,
        "baseline_sharpe": b,
        "baseline_percentile": percentile if equivalent else None,
        "baseline_percentile_raw": percentile,
        "significantly_better_than_random": bool(equivalent and percentile is not None and percentile >= 0.95),
        "deflated_sharpe": dsr if equivalent else None,
        "control_equivalence": equivalent,
        "equivalence_evidence": {
            "method": "真实 score 经 score_override 重跑，逐日净值与成本须与基准一致",
            "max_abs_equity_diff": round(max_diff, 9) if np.isfinite(max_diff) else None,
            "cost_abs_diff": round(cost_diff, 9),
            "shared_pipeline": ["build_weight_function(buffer/换手/单标的与行业上限/趋势过滤)",
                                "market_exposure(MA200 择时)", "DrawdownFuse + on_mark 强制调仓",
                                "BacktestEngine 两段式撮合 + T+1 lots FIFO + 涨跌停/停牌 + 成本"],
            "only_difference": "选股打分值（因子合成 vs 固定种子随机 rank-Z）",
        },
        "note": ("等价对照：完整复用权重函数、择时、熔断、换手与两段式撮合，仅把选股打分换成随机；"
                 "n_paths 次真实引擎重跑，非向量化近似") if equivalent else
                "等价性断言失败：分位数与 DSR 已置空，不得作为统计证据",
    }


def cost_sensitivity(md, cfg) -> dict:
    rows = []
    base = cfg.section("cost")
    for m in cfg.get("validation.cost_multipliers", [1.0, 1.5, 2.0, 3.0]):
        patch = {k: float(v) * float(m) for k, v in base.items() if isinstance(v, (int, float))}
        c = cfg.with_overrides(**{f"cost.{k}": v for k, v in patch.items()})
        res = run_backtest(md, c)["result"]
        p = _perf_from_equity(res.equity)
        rows.append({"multiplier": float(m), "total_return": p.get("total_return"),
                     "annualized_return": p.get("annualized_return"),
                     "cost_total_cny": round(res.cost_total, 2)})
    zero = [r["multiplier"] for r in rows
            if r.get("total_return") is not None and r["total_return"] <= 0]
    return {"rows": rows, "return_hits_zero_at": zero,
            "note": "成本放大后收益是否归零；冲击成本按用户设定的 none 未纳入，本项仅覆盖已建模成本"}


def attribution(md, cfg, baseline: dict) -> dict:
    """控制变量法的增量贡献（非严格 Brinson 归因，只作方向性拆解）。"""
    variants = {
        "no_market_timing": {"strategy.market_timing.below_ma_exposure": 1.0},
        "no_trend_filter": {"strategy.trend.require_close_above_ma_short": False,
                            "strategy.trend.require_ma_short_above_ma_long": False},
        "zero_cost": {"cost.commission_bps": 0.0, "cost.commission_min_cny": 0.0,
                      "cost.stamp_tax_sell_bps": 0.0, "cost.slippage_bps": 0.0},
    }
    out = {}
    for name, patch in variants.items():
        c = cfg.with_overrides(**patch)
        res = run_backtest(md, c)["result"]
        p = _perf_from_equity(res.equity)
        out[name] = {"annualized_return": p.get("annualized_return"),
                     "sharpe": p.get("sharpe"),
                     "delta_vs_baseline": round(p["annualized_return"] - baseline["annualized_return"], 6)
                     if p.get("annualized_return") is not None else None}
    return {"variants": out,
            "note": "逐项关闭某机制后的年化收益差；各变体不正交，差额不可简单相加，属方向性拆解"}


def overfit_suite(md, cfg, result, baseline: dict, score=None) -> dict:
    """过拟合四件套汇总，缺项显式登记为 None + 原因，绝不编造。"""
    suite = {
        "1_out_of_sample": oos_check(result, cfg),
        "2_parameter_sensitivity": parameter_sensitivity(md, cfg),
        "3_monte_carlo_deflated_sharpe": monte_carlo(md, cfg, result, baseline, score),
        "4_cost_sensitivity": cost_sensitivity(md, cfg),
    }
    # 专家团审核发现：仅判 `v is not None` 会让 complete 恒为 True（各函数均有兜底 dict）。
    # 改为逐项校验关键字段有效且非 error，使「四件套成立」可证伪。
    required = {
        "1_out_of_sample": ("in_sample", "out_of_sample"),
        "2_parameter_sensitivity": ("grid", "sharpe_spread"),
        "3_monte_carlo_deflated_sharpe": ("n_paths", "random_sharpe_mean", "deflated_sharpe"),
        "4_cost_sensitivity": ("rows", "return_hits_zero_at"),
    }
    missing = []
    for key, fields in required.items():
        v = suite.get(key)
        if v is None or any(v.get(f) is None for f in fields) or "error" in v:
            missing.append(key)
        if key == "2_parameter_sensitivity" and isinstance(v, dict) and v.get("grid"):
            if any("error" in row for row in v["grid"] if isinstance(row, dict)):
                if key not in missing:
                    missing.append(key)
    n_cfg = int(cfg.get("validation.mc_paths", 200))
    mc_key = "3_monte_carlo_deflated_sharpe"
    mc = suite.get(mc_key) or {}
    if (mc.get("n_paths", 0) < n_cfg or not mc.get("control_equivalence")) and mc_key not in missing:
        missing.append(mc_key)
    present = [k for k in suite if k not in missing]
    return {"suite": suite, "present": present, "missing": missing,
            "complete": len(missing) == 0,
            "note": "四件套缺任一项或关键字段无效，quality gate 不得放行策略级结论"}
