"""风险层：回撤熔断、波动/回撤、VaR/CVaR、压力测试、相关性与集中度。

压力测试区分两类：
  * 历史情景：用样本内真实收益计算（事实，标注区间与是否覆盖）；
  * 假设情景：按显式冲击参数计算（模型估计，不是历史事实，绝不混写）。
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


class DrawdownFuse:
    """净值回撤熔断：达到阈值降敞口，回撤收敛到 recovery 才恢复。"""

    def __init__(self, cfg):
        self.levels = sorted(cfg.get("risk.dd_fuse_levels", []), key=lambda x: x["dd"], reverse=True)
        self.recovery = float(cfg.get("risk.recovery_dd", 0.08))
        self.peak = 1.0
        self.exposure = 1.0
        self.events: List[dict] = []

    def update(self, date: str, equity: float) -> None:
        if equity <= 0:
            self.exposure = 0.0
            return
        self.peak = max(self.peak, equity)
        dd = 1.0 - equity / self.peak
        if self.exposure >= 1.0:
            for lvl in self.levels:
                if dd >= float(lvl["dd"]):
                    self.exposure = float(lvl["exposure"])
                    self.events.append({"date": date, "drawdown": round(dd, 4),
                                        "action": f"敞口降至 {self.exposure:.0%}"})
                    break
        elif dd <= self.recovery:
            self.exposure = 1.0
            self.events.append({"date": date, "drawdown": round(dd, 4), "action": "敞口恢复至 100%"})


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def max_drawdown_detail(equity: pd.Series) -> dict:
    dd = drawdown_series(equity)
    mdd = float(dd.min())
    trough = dd.idxmin()
    prior = equity.loc[:trough]
    peak = prior.idxmax()
    after = equity.loc[trough:]
    recovered = after[after >= equity.loc[peak]]
    rec_date = recovered.index[0] if len(recovered) else None
    return {
        "max_drawdown": round(mdd, 6),
        "peak_date": str(peak), "trough_date": str(trough),
        "drawdown_days": int((pd.to_datetime(trough) - pd.to_datetime(peak)).days),
        "recovery_date": str(rec_date) if rec_date is not None else None,
        "recovered": rec_date is not None,
    }


def historical_var_cvar(returns: pd.Series, conf: float = 0.95) -> dict:
    r = returns.dropna()
    if len(r) < 20:
        return {"confidence": conf, "var": None, "cvar": None, "n": int(len(r))}
    q = float(np.quantile(r, 1.0 - conf))
    tail = r[r <= q]
    return {"confidence": conf, "method": "historical", "window": int(len(r)),
            "var": round(q, 6), "cvar": round(float(tail.mean()), 6) if len(tail) else None}


def historical_scenarios(equity: pd.Series, windows: Dict[str, tuple]) -> dict:
    """历史情景：只在样本真实覆盖的区间内计算，未覆盖就写「未覆盖」。"""
    out = {}
    s = equity.copy()
    s.index = pd.to_datetime(s.index)
    for name, (a, b) in windows.items():
        seg = s[(s.index >= pd.Timestamp(a)) & (s.index <= pd.Timestamp(b))]
        if len(seg) < 20:
            out[name] = {"window": f"{a}~{b}", "covered": False, "note": "样本未覆盖或交易日不足20"}
            continue
        dd = float((seg / seg.cummax() - 1.0).min())
        out[name] = {"window": f"{a}~{b}", "covered": True, "trading_days": int(len(seg)),
                     "period_return": round(float(seg.iloc[-1] / seg.iloc[0] - 1.0), 6),
                     "max_drawdown": round(dd, 6)}
    return out


def hypothetical_scenarios(cfg) -> dict:
    """假设情景：显式冲击参数的模型估计，不是历史数据。"""
    return {
        "相关性跳升": {"shock": "20个交易日内组合日收益统一取样本1%分位",
                     "estimate_formula": "cum_loss = (1 + q01)^20 - 1",
                     "note": "需结合实测 1% 分位在 analytics 中给出数值"},
        "流动性枯竭": {"shock": "连续3日 -7%，且卖出额外滑点 20bp",
                     "estimate_formula": "(1-0.07)^3 - 1 - 0.002*turnover",
                     "note": "模型估计，非历史事实"},
        "连续跌停": {"shock": "持仓连续2个跌停（主板 -10%）",
                    "estimate_formula": "(1-0.10)^2 - 1",
                    "static_estimate": round(0.9 ** 2 - 1.0, 6)},
        "趋势择时失效": {"shock": "大盘 MA200 信号反向，暴露打 5 折持续 3 个月",
                     "note": "在 analytics 的 no_timing 对照中量化"},
    }


def correlation_shift(md, worst_n: int = 40) -> dict:
    """正常期 vs 最差 N 日的平均两两相关性（事实，来自真实收益）。"""
    r = md.ret.fillna(0.0)
    daily_mean = r.mean(axis=1)
    worst_days = daily_mean.nsmallest(min(worst_n, len(daily_mean))).index
    def avg_corr(frame: pd.DataFrame) -> float | None:
        c = frame.corr().values
        iu = np.triu_indices_from(c, k=1)
        vals = c[iu]
        vals = vals[np.isfinite(vals)]
        return round(float(vals.mean()), 4) if len(vals) else None
    return {
        "full_sample_avg_pair_corr": avg_corr(r),
        "worst_days_avg_pair_corr": avg_corr(r.loc[worst_days]),
        "worst_n": int(len(worst_days)),
        "note": "极端行情相关性是否跳升，用同一面板计算",
    }


def risk_report(result, md, cfg) -> dict:
    r = result.returns
    dd = max_drawdown_detail(result.equity)
    var = {str(c): historical_var_cvar(r, c) for c in cfg.get("risk.var_conf", [0.95, 0.99])}
    # 相关性跳升情景用真实 1% 分位补齐数值
    q01 = float(np.quantile(r.dropna(), 0.01)) if r.dropna().size > 20 else None
    hypo = hypothetical_scenarios(cfg)
    if q01 is not None:
        hypo["相关性跳升"]["estimated_20d_loss"] = round(float((1 + q01) ** 20 - 1), 6)
    windows = {
        "2018 贸易摩擦": ("2018-01-01", "2018-12-31"),
        "2020 疫情冲击": ("2020-01-01", "2020-06-30"),
        "2022 调整周期": ("2022-01-01", "2022-12-31"),
        "2024 样本外起点后": ("2024-01-01", "2024-12-31"),
    }
    return {
        "position_leverage": {
            "leverage_enabled": bool(cfg.get("leverage.enabled", False)),
            "max_leverage": float(cfg.get("leverage.max", 1.0)),
            "short_allowed": bool(cfg.get("leverage.short", False)),
            "target_exposure": "满仓（受 MA200 择时与回撤熔断缩放）",
            "margin_call_boundary": "不适用：无杠杆、无融资融券",
        },
        "stop_rules": {"type": "回撤熔断（非个股固定止损）",
                       "levels": cfg.get("risk.dd_fuse_levels", []),
                       "recovery_dd": cfg.get("risk.recovery_dd", 0.08),
                       "triggered_events": result.config_snapshot.get("fuse_events", [])},
        "vol_drawdown": {
            "annualized_vol": round(float(r.std(ddof=1) * np.sqrt(252)), 6),
            "rolling_vol_20d_latest": round(float(r.tail(20).std(ddof=1) * np.sqrt(252)), 6),
            **dd,
        },
        "tail": var,
        "stress": {"historical": historical_scenarios(result.equity, windows),
                   "hypothetical": hypo},
        "correlation_concentration": correlation_shift(md),
        "data_gaps": cfg.get("data.known_gaps", []),
    }
