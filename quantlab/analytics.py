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
    p.update({
        "cost_total_cny": round(result.cost_total, 2),
        "cost_breakdown_cny": result.cost_breakdown,
        "gross_profit_proxy_cny": round(gross, 2),
        "cost_share_of_gross": round(result.cost_total / gross, 6) if gross > 0 else None,
        "cost_share_note": "毛收益=净收益+全部成本；若毛收益<=0 则占比不适用" if gross <= 0 else "",
        "avg_turnover_per_rebalance": round(float(np.mean(list(result.turnover_history.values()))), 4)
        if result.turnover_history else None,
        "blocked_orders": int(result.blocked_count),
        "n_trades": int(len(result.trades)),
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
        "note": "样本外仅使用 1 次；任何基于样本外结果回改参数的行为都必须递增计数并重新定级",
    }


def parameter_sensitivity(md, cfg) -> dict:
    grid = cfg.get("validation.sensitivity_grid", {"top_n": [5, 8, 12], "ma_long": [90, 120, 160]})
    rows = []
    for n in grid.get("top_n", [8]):
        for ma in grid.get("ma_long", [120]):
            c = cfg.with_overrides(**{"strategy.top_n": int(n), "strategy.trend.ma_long": int(ma)})
            try:
                res = run_backtest(md, c)["result"]
                p = _perf_from_equity(res.equity)
                rows.append({"top_n": int(n), "ma_long": int(ma),
                             "annualized_return": p.get("annualized_return"),
                             "sharpe": p.get("sharpe"), "max_drawdown": p.get("max_drawdown")})
            except Exception as exc:
                rows.append({"top_n": int(n), "ma_long": int(ma), "error": f"{type(exc).__name__}: {exc}"})
    sh = [r["sharpe"] for r in rows if r.get("sharpe") is not None]
    spread = round(max(sh) - min(sh), 4) if len(sh) >= 2 else None
    cliff = bool(spread is not None and (spread > 0.8 or (min(sh) < 0 < max(sh))))
    return {"grid": rows, "sharpe_spread": spread, "cliff_like_decay": cliff,
            "note": "邻域网格；悬崖式衰减判据：夏普极差>0.8 或正负号翻转"}


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


def monte_carlo(md, cfg, baseline: dict) -> dict:
    """随机策略对照：向量化快速模拟，保持同等调仓频率/持仓数/成本口径。

    与基准的差别仅在选股（随机 vs 因子打分）；净值按等权持有期收益逐日复利。
    """
    from .strategy import rebalance_dates
    n_paths = int(cfg.get("validation.mc_paths", 200))
    start, end = cfg.get("period.start"), cfg.get("period.end")
    dates = [d for d in md.dates if start <= d <= end]
    reb = set(rebalance_dates(md, start, end))
    R = md.ret.fillna(0.0).reindex(index=dates, columns=md.codes).to_numpy(dtype=float)
    E = md.eligible.reindex(index=dates, columns=md.codes).fillna(False).to_numpy(dtype=bool)
    top_n = int(cfg.get("strategy.top_n", 8))
    # 单次完整换仓的近似成本（双边佣金+印花税+滑点，按 bps）
    cost_bps = (2 * float(cfg.get("cost.commission_bps", 2.5))
                + float(cfg.get("cost.stamp_tax_sell_bps", 10.0))
                + 2 * float(cfg.get("cost.slippage_bps", 5.0)))
    sharpes = []
    for i in range(n_paths):
        rng = np.random.default_rng(9000 + i)
        eq, curve = 1.0, np.empty(len(dates), dtype=float)
        picks = None
        pending_cost = 0.0
        for j, d in enumerate(dates):
            if picks is not None:
                day_r = float(np.nanmean(R[j, picks])) - pending_cost
                pending_cost = 0.0
                eq *= (1.0 + day_r)
            curve[j] = eq
            if d in reb:
                pool = np.flatnonzero(E[j])
                if pool.size:
                    picks = rng.choice(pool, size=min(top_n, pool.size), replace=False)
                    pending_cost = cost_bps / 10_000.0
        s = pd.Series(curve)
        sr = s.pct_change().dropna()
        sd = float(sr.std(ddof=1)) if len(sr) > 1 else 0.0
        if sd > 0:
            sharpes.append(float(sr.mean() / sd * math.sqrt(252)))
    arr = np.array(sharpes, dtype=float)
    b = baseline.get("sharpe")
    trials = int(cfg.get("validation.trials_factor_count", 6)) * int(cfg.get("validation.trials_param_grid", 9))
    dsr = deflated_sharpe(baseline.get("daily_sharpe"), baseline.get("trading_days", 0), trials,
                          baseline.get("skew", 0.0), baseline.get("kurtosis", 3.0))
    return {
        "n_paths": int(len(arr)),
        "random_sharpe_mean": round(float(arr.mean()), 4) if arr.size else None,
        "random_sharpe_std": round(float(arr.std(ddof=1)), 4) if arr.size > 1 else None,
        "random_sharpe_p95": round(float(np.quantile(arr, 0.95)), 4) if arr.size else None,
        "baseline_sharpe": b,
        "baseline_percentile": round(float((arr < b).mean()), 4) if arr.size and b is not None else None,
        "deflated_sharpe": dsr,
        "note": "随机对照组：同等调仓频率、同等持仓数、同等成本，仅把选股换成随机",
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


def overfit_suite(md, cfg, result, baseline: dict) -> dict:
    """过拟合四件套汇总，缺项显式登记为 None + 原因，绝不编造。"""
    suite = {
        "1_out_of_sample": oos_check(result, cfg),
        "2_parameter_sensitivity": parameter_sensitivity(md, cfg),
        "3_monte_carlo_deflated_sharpe": monte_carlo(md, cfg, baseline),
        "4_cost_sensitivity": cost_sensitivity(md, cfg),
    }
    present = [k for k, v in suite.items() if v is not None]
    missing = [k for k, v in suite.items() if v is None]
    return {"suite": suite, "present": present, "missing": missing,
            "complete": len(missing) == 0,
            "note": "四件套缺任一项，quality gate 不得放行策略级结论"}
