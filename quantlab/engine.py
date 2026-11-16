"""回测引擎：事件驱动日频循环，T 日收盘出信号、T+1 开盘撮合。

已建模的 A 股约束：
  * T+1（当日买入不可当日卖出）
  * 涨跌停不可成交（按板块 10% / 20% / 30%，ST 直接剔除）
  * 停牌 / 无成交不可成交
  * 佣金双边万2.5（最低¥5）、印花税千1仅卖出、滑点5bp
  * 前复权价格用于收益计算；成交额为近似值（见数据合约）

明确未建模（登记为风险，不假装已处理）：冲击成本、整手取整、分红再投资、
集合竞价撮合、退市清算、融券与杠杆、真实 Level-2 盘口。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List

import numpy as np
import pandas as pd


@dataclass
class Trade:
    date: str
    code: str
    side: str
    shares: float
    price: float
    gross: float
    commission: float
    tax: float
    slippage: float
    blocked: str = ""


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: pd.DataFrame
    weights_history: Dict[str, pd.Series] = field(default_factory=dict)
    turnover_history: Dict[str, float] = field(default_factory=dict)
    blocked_count: int = 0
    cost_total: float = 0.0
    cost_breakdown: dict = field(default_factory=dict)
    config_snapshot: dict = field(default_factory=dict)

    @property
    def returns(self) -> pd.Series:
        return self.equity.pct_change().fillna(0.0)


class BacktestEngine:
    def __init__(self, md, cfg, weights_fn: Callable[[str], pd.Series], rebal_dates: List[str]):
        self.md = md
        self.cfg = cfg
        self.weights_fn = weights_fn
        self.rebal_set = set(rebal_dates)
        self.on_mark: Callable[[str, float], None] | None = None
        self.close_ffill = md.close.ffill()

    def run(self) -> BacktestResult:
        cfg, md = self.cfg, self.md
        start, end = cfg.get("period.start"), cfg.get("period.end")
        dates = [d for d in md.dates if start <= d <= end]
        if len(dates) < 30:
            raise ValueError("有效交易日不足 30 天，无法回测")

        capital = float(cfg.get("validation.research_capital_cny", 1_000_000.0))
        comm_bps = float(cfg.get("cost.commission_bps", 2.5))
        comm_min = float(cfg.get("cost.commission_min_cny", 5.0))
        tax_bps = float(cfg.get("cost.stamp_tax_sell_bps", 10.0))
        slip = float(cfg.get("cost.slippage_bps", 5.0)) / 10_000.0

        cash = capital
        positions: Dict[str, float] = {}
        bought_today: Dict[str, float] = {}
        equity: Dict[str, float] = {}
        trades: List[Trade] = []
        weights_history: Dict[str, pd.Series] = {}
        turnover_history: Dict[str, float] = {}
        cost = {"commission": 0.0, "tax": 0.0, "slippage": 0.0}
        blocked_count = 0
        pending: pd.Series | None = None
        pending_date: str | None = None
        last_target = pd.Series(dtype=float)

        for d in dates:
            op = md.open.loc[d]
            bought_today = {}

            # ---- 1) 执行上一交易日收盘生成的目标权重（T+1 开盘） ----
            if pending is not None:
                px_open = {c: (op.get(c) if pd.notna(op.get(c, np.nan)) else self.close_ffill.loc[d, c])
                           for c in set(list(positions) + list(pending.index))}
                pv_open = cash + sum(sh * float(px_open.get(c, 0.0)) for c, sh in positions.items())
                all_codes = sorted(set(list(positions) + list(pending.index)))
                for c in all_codes:
                    o = op.get(c, np.nan)
                    if not pd.notna(o) or o <= 0:
                        blocked_count += 1
                        trades.append(Trade(pending_date or d, c, "SKIP", 0, 0.0, 0.0, 0.0, 0.0, 0.0, "停牌/无开盘价"))
                        continue
                    target_sh = float(pending.get(c, 0.0)) * pv_open / (float(o) * (1.0 + slip))
                    cur_sh = float(positions.get(c, 0.0))
                    delta = target_sh - cur_sh
                    if abs(delta) < 1e-10:
                        continue
                    if delta > 0:
                        if not bool(md.can_buy.loc[d].get(c, False)):
                            blocked_count += 1
                            trades.append(Trade(pending_date or d, c, "BUY_BLOCKED", 0, float(o), 0.0, 0.0, 0.0, 0.0, "涨停/不可买"))
                            continue
                        price = float(o) * (1.0 + slip)
                        gross = delta * price
                        fee = max(gross * comm_bps / 10_000.0, comm_min)
                        cash -= gross + fee
                        positions[c] = cur_sh + delta
                        bought_today[c] = bought_today.get(c, 0.0) + delta
                        cost["commission"] += fee
                        cost["slippage"] += delta * float(o) * slip
                        trades.append(Trade(d, c, "BUY", delta, price, gross, fee, 0.0, delta * float(o) * slip))
                    else:
                        if not bool(md.can_sell.loc[d].get(c, False)):
                            blocked_count += 1
                            trades.append(Trade(pending_date or d, c, "SELL_BLOCKED", 0, float(o), 0.0, 0.0, 0.0, 0.0, "跌停/不可卖"))
                            continue
                        sellable = max(0.0, cur_sh - float(bought_today.get(c, 0.0)))
                        sh = min(-delta, sellable)          # T+1：当日买入部分不可卖
                        if sh <= 0:
                            blocked_count += 1
                            trades.append(Trade(d, c, "SELL_BLOCKED", 0, float(o), 0.0, 0.0, 0.0, 0.0, "T+1 锁定"))
                            continue
                        price = float(o) * (1.0 - slip)
                        gross = sh * price
                        fee = max(gross * comm_bps / 10_000.0, comm_min)
                        tax = gross * tax_bps / 10_000.0
                        cash += gross - fee - tax
                        positions[c] = cur_sh - sh
                        cost["commission"] += fee
                        cost["tax"] += tax
                        cost["slippage"] += sh * float(o) * slip
                        trades.append(Trade(d, c, "SELL", sh, price, gross, fee, tax, sh * float(o) * slip))
                positions = {c: s for c, s in positions.items() if s > 1e-9}
                union = pending.index.union(last_target.index)
                turnover_history[d] = float(
                    (pending.reindex(union).fillna(0.0) - last_target.reindex(union).fillna(0.0)).abs().sum() / 2.0
                )
                last_target = pending.copy()
                pending, pending_date = None, None

            # ---- 2) 收盘盯市 ----
            cl = self.close_ffill.loc[d]
            pv = cash + sum(sh * float(cl.get(c, 0.0)) for c, sh in positions.items())
            equity[d] = pv
            if self.on_mark:
                self.on_mark(d, pv)

            # ---- 3) 收盘生成下一交易日的目标权重 ----
            if d in self.rebal_set:
                w = self.weights_fn(d)
                weights_history[d] = w.copy()
                pending, pending_date = w, d

        eq = pd.Series(equity, name="equity")
        tdf = pd.DataFrame([t.__dict__ for t in trades])
        total_cost = float(sum(cost.values()))
        return BacktestResult(
            equity=eq, trades=tdf, weights_history=weights_history,
            turnover_history=turnover_history, blocked_count=blocked_count,
            cost_total=total_cost,
            cost_breakdown={k: round(v, 2) for k, v in cost.items()},
            config_snapshot={"cost": cfg.section("cost"), "period": cfg.section("period"),
                             "strategy": cfg.section("strategy")},
        )
