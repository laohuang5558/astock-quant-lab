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
    """净值回撤熔断：达到阈值逐级降敞口（升档），回撤收敛才恢复。

    状态机修复要点（针对旧版三处缺陷）：
      * 旧版 ``if exposure >= 1.0`` 只在满仓档判定降档，降档后回撤继续扩大也不再升档；
        新版允许从**任何档位逐级升档**，含一路升到 0.0 清仓档。
      * 旧版清仓(exposure=0)后组合为现金、真实净值冻结、peak 只增不减 → 恢复条件
        ``dd <= recovery`` 永远不成立，永久锁死。新版用「影子净值」判断恢复：
        ``shadow_equity`` 始终按市场收益复利（与是否清仓无关），清仓档以影子净值
        回撤收敛作为恢复路径。
      * 恢复时把真实回撤基准 peak 重置为恢复当日净值（重新入场点位），避免清仓冻结
        净值使真实 dd 恒大于阈值而即时误升档；影子净值不重置，继续跟踪市场。
    """

    def __init__(self, cfg):
        self.levels = sorted(cfg.get("risk.dd_fuse_levels", []), key=lambda x: x["dd"], reverse=True)
        self.recovery = float(cfg.get("risk.recovery_dd", 0.08))
        self.peak = 1.0
        self.exposure = 1.0
        self.events: List[dict] = []
        # 敞口档位阶梯：[满仓 1.0, 较轻档..., 清仓 0.0]，逐级降敞口
        self._rungs = [1.0] + [e for e in sorted(
            {float(l["exposure"]) for l in self.levels}, reverse=True) if e < 1.0 - 1e-9]
        # 每个降档档位对应的回撤阈值（同敞口多档取更易触发者）
        self._rung_dd: Dict[float, float] = {}
        for lvl in self.levels:
            e = float(lvl["exposure"])
            self._rung_dd[e] = min(self._rung_dd.get(e, float("inf")), float(lvl["dd"]))
        # 影子净值：始终满仓市场指数的假想净值（market_return 由 update 传入）
        self.shadow_equity = 1.0
        self.shadow_peak = 1.0
        self._shadow_active = False

    def _rung_index(self) -> int:
        return min(range(len(self._rungs)),
                   key=lambda i: abs(self._rungs[i] - self.exposure))

    def _append_event(self, date: str, dd: float, action: str) -> None:
        # 向后兼容：保留 date / drawdown / action 三键；新增 trigger_date / effective_date
        # 区分「触发日」与「生效日」概念，并登记 T+1 撮合口径。
        self.events.append({
            "date": date,                 # 兼容旧调用方：信号 / 触发日
            "trigger_date": date,         # 触发日：达到阈值 / 满足恢复条件的当日收盘
            "effective_date": date,       # 状态机生效日（目标敞口由引擎在 T+1 开盘撮合）
            "drawdown": round(dd, 4),
            "action": action,
            "exposure": round(float(self.exposure), 4),
            "note": "信号于当日收盘触发，目标敞口由回测引擎在 T+1 开盘撮合生效",
        })

    def update(self, date: str, equity: float, market_return: float = 0.0) -> None:
        """按日更新熔断状态。

        market_return: 市场（等权样本指数）当日收益，收盘可知、无未来函数；默认 0.0
        以兼容旧版两参调用（此时退化为真实净值口径、无影子恢复路径）。
        """
        mr = float(market_return)
        if mr != 0.0:
            self._shadow_active = True
        self.shadow_equity *= (1.0 + mr)
        self.shadow_peak = max(self.shadow_peak, self.shadow_equity)
        shadow_dd = (1.0 - self.shadow_equity / self.shadow_peak) if self.shadow_peak > 0 else 0.0

        if equity <= 0:
            self.exposure = self._rungs[-1]   # 最严重档（清仓）
            return
        self.peak = max(self.peak, equity)
        dd = 1.0 - equity / self.peak        # 真实组合回撤：升档判定 + 事件读数（事实口径）

        cur = self._rung_index()
        max_rung = len(self._rungs) - 1
        # 目标档位：按真实回撤命中的最严重档位
        target = 0
        for i in range(1, len(self._rungs)):
            if dd >= self._rung_dd.get(self._rungs[i], float("inf")):
                target = i

        if target > cur:
            # 逐级升档：每次只降一个档位，可从任意档位继续升到 0.0 清仓档
            self.exposure = self._rungs[cur + 1]
            self._append_event(date, dd, f"敞口逐级降至 {self.exposure:.0%}")
        elif cur > 0:
            # 恢复：① 真实净值回撤收敛（非清仓档常规恢复路径，向后兼容）；
            #       ② 清仓档额外允许「影子净值回撤收敛」作为恢复路径，使清仓档不再锁死。
            recovered_by_real = dd <= self.recovery
            recovered_by_shadow = (
                cur == max_rung and self._shadow_active and shadow_dd <= self.recovery)
            if recovered_by_real or recovered_by_shadow:
                self.exposure = 1.0
                self.peak = equity            # 重置回撤基准，避免冻结净值即时误升档
                self._append_event(date, dd, "敞口恢复至 100%")


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
    """历史情景：**全局峰值口径**——先按全样本 equity.cummax() 算逐日回撤，再切窗口取最小值。

    旧版在窗口内用 seg.cummax() 重置峰值，会把窗口外（如 2021-02）峰值之后的持续
    深回撤抹平；全局口径保留窗口外峰值，窗口内回撤不再被重置。样本不足时按实际
    覆盖区间标注（sample_start / sample_end）。
    """
    out = {}
    s = equity.copy()
    s.index = pd.to_datetime(s.index)
    # 全样本累计峰值 → 逐日回撤（峰值只来自该日及之前，无未来函数）
    dd_series = s / s.cummax() - 1.0
    for name, (a, b) in windows.items():
        a_ts, b_ts = pd.Timestamp(a), pd.Timestamp(b)
        mask = (s.index >= a_ts) & (s.index <= b_ts)
        seg = s[mask]
        if len(seg) < 20:
            out[name] = {"window": f"{a}~{b}", "covered": False,
                         "note": "样本未覆盖或交易日不足20", "sample_days": int(len(seg))}
            continue
        dd = float(dd_series[mask].min())
        sample_start, sample_end = str(seg.index.min().date()), str(seg.index.max().date())
        out[name] = {"window": f"{a}~{b}", "covered": True, "trading_days": int(len(seg)),
                     "period_return": round(float(seg.iloc[-1] / seg.iloc[0] - 1.0), 6),
                     "max_drawdown": round(dd, 6),
                     "sample_start": sample_start, "sample_end": sample_end,
                     "note": f"全局峰值口径(equity.cummax 全样本)；样本实际覆盖 {sample_start}~{sample_end}"}
    return out


def hypothetical_scenarios(cfg) -> dict:
    """假设情景：显式冲击参数的模型估计，不是历史数据。"""
    # 流动性枯竭显式公式：(1-0.07)^3 - 1 - 0.002*turnover，turnover 写死为 1.0
    # （压力期内组合全部换手一次的明确假设）。
    turnover_assumption = 1.0
    liquidity_est = round((1 - 0.07) ** 3 - 1.0 - 0.002 * turnover_assumption, 6)
    return {
        "相关性跳升": {"shock": "20个交易日内组合日收益统一取样本1%分位",
                     "estimate_formula": "cum_loss = (1 + q01)^20 - 1",
                     "note": "由 risk_report 用实测 1% 分位 q01 代入上式得到 estimated_20d_loss；模型估计，非历史事实"},
        "流动性枯竭": {"shock": "连续3日 -7%，且卖出额外滑点 20bp",
                     "estimate_formula": "(1-0.07)^3 - 1 - 0.002*turnover",
                     "turnover_assumption": turnover_assumption,
                     "static_estimate": liquidity_est,
                     "note": f"显式公式估计：turnover={turnover_assumption}（写死假设）；模型估计，非历史事实"},
        "连续跌停": {"shock": "持仓连续2个跌停（主板 -10%）",
                    "estimate_formula": "(1-0.10)^2 - 1",
                    "static_estimate": round(0.9 ** 2 - 1.0, 6),
                    "note": "模型估计，非历史事实"},
        "趋势择时失效": {"shock": "大盘 MA200 信号反向，暴露打 5 折持续 3 个月",
                     "estimate_formula": "no_timing 对照组相对基准的年化收益差 delta",
                     "static_estimate": -0.027567,
                     "note": "来源：专家团独立重放给定的 no_timing 对照年化收益差已实测值 -0.027567（非本函数实时计算）；模型估计，非历史事实"},
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
        # 真实极值段：2021-02-10 峰值之后的持续深回撤（全局峰值口径下不再被窗口重置抹平）
        "2021 峰值后持续深回撤": ("2021-02-10", "2026-08-17"),
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
