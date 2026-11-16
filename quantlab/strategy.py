"""策略层：信号合成 → 趋势过滤 → 组合权重 → 调仓与降级熔断。

职责边界：只产出「目标权重」，不实现撮合、不做仓位/止损/VaR 计算（见 engine / risk）。
"""
from __future__ import annotations

from typing import Callable, Dict

import numpy as np
import pandas as pd


def rebalance_dates(md, start: str, end: str) -> list[str]:
    """月末最后一个交易日生成信号，T+1 开盘执行。"""
    s = pd.Series([d for d in md.dates if start <= d <= end])
    if s.empty:
        return []
    dt = pd.to_datetime(s)
    key = dt.dt.strftime("%Y-%m")
    last = s.groupby(key).max().tolist()
    return sorted(last)


def moving_average(df: pd.DataFrame, window: int) -> pd.DataFrame:
    return df.rolling(window, min_periods=max(2, int(window * 0.6))).mean()


def trend_mask(md, cfg) -> pd.DataFrame:
    """个股趋势过滤：close > MA_short 且 MA_short > MA_long。"""
    s = int(cfg.get("strategy.trend.ma_short", 60))
    l = int(cfg.get("strategy.trend.ma_long", 120))
    ma_s, ma_l = moving_average(md.close, s), moving_average(md.close, l)
    ok = pd.DataFrame(True, index=md.close.index, columns=md.close.columns)
    if cfg.get("strategy.trend.require_close_above_ma_short", True):
        ok &= md.close > ma_s
    if cfg.get("strategy.trend.require_ma_short_above_ma_long", True):
        ok &= ma_s > ma_l
    return ok.fillna(False)


def market_proxy(md) -> pd.Series:
    """等权样本指数（生产环境应替换为中证全指/万得全A，见 docs/STRATEGY_SPEC.md）。"""
    r = md.ret.mean(axis=1).fillna(0.0)
    return (1.0 + r).cumprod()


def market_exposure(md, cfg) -> pd.Series:
    """大盘择时：等权样本指数在 MA200 之上满仓，之下降到 below_ma_exposure。"""
    w = int(cfg.get("strategy.market_timing.ma_window", 200))
    below = float(cfg.get("strategy.market_timing.below_ma_exposure", 0.5))
    px = market_proxy(md)
    ma = px.rolling(w, min_periods=max(2, int(w * 0.6))).mean()
    return pd.Series(np.where(px > ma, 1.0, below), index=px.index).fillna(below)


def _cap_weights(w: pd.Series, cap: float) -> pd.Series:
    """迭代等权上限裁剪并重新归一化。"""
    if w.empty:
        return w
    w = w.clip(upper=cap)
    for _ in range(12):
        total = w.sum()
        if total <= 1.0 + 1e-9:
            break
        w = (w / total).clip(upper=cap)
    return w / w.sum() if w.sum() > 0 else w


def build_weight_function(md, cfg, score: pd.DataFrame, fuse_fn: Callable[[str], float]):
    """返回 weights_fn(date) -> Series，供回测引擎在 T 日收盘调用、T+1 开盘执行。"""
    tmask = trend_mask(md, cfg)
    mexpo = market_exposure(md, cfg)
    top_n = int(cfg.get("strategy.top_n", 8))
    buf_mult = float(cfg.get("strategy.buffer_multiple", 1.5))
    max_w = float(cfg.get("strategy.max_weight", 0.20))
    max_turn = float(cfg.get("strategy.max_turnover_per_rebalance", 0.60))
    state: Dict[str, pd.Series] = {"last": pd.Series(dtype=float)}

    def weights_fn(date: str) -> pd.Series:
        if date not in score.index or date not in mexpo.index:
            return state["last"]
        row = score.loc[date]
        elig = md.eligible.loc[date].reindex(row.index).fillna(False)
        t_ok = tmask.loc[date].reindex(row.index).fillna(False)
        valid = row[elig & t_ok & row.notna()].sort_values(ascending=False)
        if valid.empty:
            target = pd.Series(dtype=float)
        else:
            buffer_n = max(top_n, int(round(top_n * buf_mult)))
            keep_ranks = set(valid.index[:buffer_n])
            prev = state["last"]
            kept = [c for c in prev.index if prev.get(c, 0) > 0 and c in keep_ranks]
            selected = list(dict.fromkeys(kept + valid.index.tolist()))[:top_n]
            w = pd.Series(1.0 / max(1, len(selected)), index=selected, dtype=float)
            w = _cap_weights(w, max_w)
            target = w * float(mexpo.loc[date]) * float(fuse_fn(date))
        # 换手约束：超限则与上一期权重线性混合
        prev = state["last"]
        all_codes = target.index.union(prev.index)
        t = target.reindex(all_codes).fillna(0.0)
        p = prev.reindex(all_codes).fillna(0.0)
        turnover = float((t - p).abs().sum() / 2.0)
        if turnover > max_turn and turnover > 0:
            lam = max_turn / turnover
            t = p + lam * (t - p)
        t = t[t > 1e-8]
        state["last"] = t
        return t

    return weights_fn


def weight_concentration(weights: pd.Series) -> dict:
    if weights.empty:
        return {"hhi": None, "top3": None, "n_positions": 0}
    s = weights / weights.sum()
    return {"hhi": round(float((s ** 2).sum()), 4),
            "top3": round(float(s.nlargest(3).sum()), 4),
            "n_positions": int((s > 0).sum())}
