"""回测引擎：事件驱动日频循环，T 日收盘出信号、T+1 开盘撮合。

已建模的 A 股约束：
  * T+1（当日买入不可当日卖出）——按持仓批次 lots 记录买入日，卖出时 FIFO 消耗
  * 涨跌停不可成交（按板块 10% / 20% / 30%，ST 直接剔除）
  * 停牌 / 无成交不可成交
  * 佣金双边万2.5（最低¥5）、印花税千1仅卖出、滑点5bp
  * 前复权价格用于收益计算；成交额为近似值（见数据合约）
  * 纯多头、无杠杆：两段式撮合（先卖后买）+ 买入按可用现金等比例缩放，
    并逐日断言 cash >= -1e-6

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
    # 目标（收盘信号）口径：weights_fn 产出的目标权重，并非实际成交持仓。
    # 显式更名为 target_* 以澄清口径；同时保留原名 weights_history/turnover_history
    # 指向同一份数据，兼容现有调用方（analytics.performance / report）。
    weights_history: Dict[str, pd.Series] = field(default_factory=dict)
    turnover_history: Dict[str, float] = field(default_factory=dict)
    target_weights_history: Dict[str, pd.Series] = field(default_factory=dict)
    target_turnover_history: Dict[str, float] = field(default_factory=dict)
    # 逐日现金 / 敞口 / 被拒订单记录（供 analytics / report 消费）
    cash_history: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    exposure_history: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    unfilled_history: Dict[str, dict] = field(default_factory=dict)
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
        # 熔断敞口变化当日强制生成一次新目标 pending（pipeline 检测到变化后置位），
        # 在 step 3 消费、T+1 开盘执行；避免只在月末调仓日生效造成的滞后。
        self.force_rebalance: bool = False
        self.close_ffill = md.close.ffill()
        # 供单测 / 诊断读取的最终状态
        self.positions: Dict[str, float] = {}
        self.lots: Dict[str, List[list]] = {}

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
        # T+1 持仓批次：code -> [[买入日, 股数], ...]（FIFO 顺序，列表便于部分消耗）
        lots: Dict[str, List[list]] = {}
        equity: Dict[str, float] = {}
        cash_series: Dict[str, float] = {}
        exposure_series: Dict[str, float] = {}
        trades: List[Trade] = []
        target_weights_history: Dict[str, pd.Series] = {}
        target_turnover_history: Dict[str, float] = {}
        # 被拒订单：持久记录（写入 BacktestResult）+ 待并入下一目标的 carry 队列
        unfilled_history: Dict[str, dict] = {}
        carry: Dict[str, float] = {}
        cost = {"commission": 0.0, "tax": 0.0, "slippage": 0.0}
        blocked_count = 0
        pending: pd.Series | None = None
        pending_date: str | None = None
        last_target = pd.Series(dtype=float)

        def record_unfilled(code: str, weight: float, reason: str, day: str) -> None:
            """登记一笔被拒目标：持久记录 + 放入 carry 等待并入下一个 pending。"""
            carry[code] = float(weight)
            info = unfilled_history.get(code)
            if info is None:
                info = {"target_weight": float(weight), "reason": reason,
                        "count": 0, "dates": []}
                unfilled_history[code] = info
            info["target_weight"] = float(weight)
            info["reason"] = reason
            info["count"] = int(info["count"]) + 1
            info["dates"].append(day)

        def assert_cash(day: str, cur_cash: float) -> None:
            """纯多头 / 无杠杆硬约束：违例抛带日期与金额的明确异常。"""
            if cur_cash < -1e-6:
                raise AssertionError(
                    f"违反「纯多头、无杠杆」约束：现金为负 日期={day} "
                    f"cash={cur_cash:.6f}（阈值 -1e-6）"
                )

        for d in dates:
            op = md.open.loc[d]

            # ---- 1) 执行上一交易日收盘生成的目标权重（T+1 开盘）----
            if pending is not None:
                px_open = {c: (op.get(c) if pd.notna(op.get(c, np.nan)) else self.close_ffill.loc[d, c])
                           for c in set(list(positions) + list(pending.index))}
                pv_open = cash + sum(sh * float(px_open.get(c, 0.0)) for c, sh in positions.items())
                all_codes = sorted(set(list(positions) + list(pending.index)))

                # 先算出每个标的的目标股数：沿用原公式 w*pv_open/(开盘价*(1+滑点))，
                # 成本公式与 Trade 记录格式保持不变。
                targets: Dict[str, float] = {}
                tradable_price: Dict[str, float] = {}
                for c in all_codes:
                    o = op.get(c, np.nan)
                    cur_sh = float(positions.get(c, 0.0))
                    w = float(pending.get(c, 0.0))
                    if not pd.notna(o) or o <= 0:
                        blocked_count += 1
                        trades.append(Trade(pending_date or d, c, "SKIP", 0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                            "停牌/无开盘价"))
                        record_unfilled(c, w, "停牌/无开盘价", d)
                        targets[c] = cur_sh            # 无法交易，维持现持仓
                        continue
                    tradable_price[c] = float(o)
                    targets[c] = w * pv_open / (float(o) * (1.0 + slip))

                # ---- 第一段：只执行所有 SELL/减仓，先回笼现金 ----
                for c in all_codes:
                    if c not in tradable_price:
                        continue
                    o = tradable_price[c]
                    cur_sh = float(positions.get(c, 0.0))
                    target_sh = targets[c]
                    delta = target_sh - cur_sh
                    if delta >= -1e-10:
                        continue
                    if not bool(md.can_sell.loc[d].get(c, False)):
                        blocked_count += 1
                        trades.append(Trade(pending_date or d, c, "SELL_BLOCKED", 0, float(o),
                                            0.0, 0.0, 0.0, 0.0, "跌停/不可卖"))
                        record_unfilled(c, float(pending.get(c, 0.0)), "跌停/不可卖", d)
                        continue
                    # T+1：只卖出买入日 < d 的批次（当日买入不可当日卖出）
                    sellable = sum(sh for (bd, sh) in lots.get(c, []) if bd < d)
                    sh = min(-delta, sellable)
                    if sh <= 0:
                        blocked_count += 1
                        trades.append(Trade(d, c, "SELL_BLOCKED", 0, float(o),
                                            0.0, 0.0, 0.0, 0.0, "T+1 锁定"))
                        record_unfilled(c, float(pending.get(c, 0.0)), "T+1 锁定", d)
                        continue
                    price = float(o) * (1.0 - slip)
                    gross = sh * price
                    fee = max(gross * comm_bps / 10_000.0, comm_min)
                    tax = gross * tax_bps / 10_000.0
                    cash += gross - fee - tax
                    positions[c] = cur_sh - sh
                    # FIFO 消耗 lot（含部分成交）；当日(bd>=d)批次不参与
                    remaining = sh
                    new_lots: List[list] = []
                    for lot in lots.get(c, []):
                        bd, lsh = lot
                        if bd >= d or remaining <= 0:
                            new_lots.append(lot)
                            continue
                        take = min(lsh, remaining)
                        lsh -= take
                        remaining -= take
                        if lsh > 1e-12:
                            new_lots.append([bd, lsh])
                    lots[c] = new_lots
                    cost["commission"] += fee
                    cost["tax"] += tax
                    cost["slippage"] += sh * float(o) * slip
                    trades.append(Trade(d, c, "SELL", sh, price, gross, fee, tax,
                                        sh * float(o) * slip))

                # ---- 第二段：只执行 BUY/加仓，按当时可用现金等比例缩放 ----
                buy_deltas: Dict[str, float] = {}
                for c in all_codes:
                    if c not in tradable_price:
                        continue
                    cur_sh = float(positions.get(c, 0.0))   # 第一段卖出后的持仓
                    target_sh = targets[c]
                    delta = target_sh - cur_sh
                    if delta <= 1e-10:
                        continue
                    o = tradable_price[c]
                    if not bool(md.can_buy.loc[d].get(c, False)):
                        blocked_count += 1
                        trades.append(Trade(pending_date or d, c, "BUY_BLOCKED", 0, float(o),
                                            0.0, 0.0, 0.0, 0.0, "涨停/不可买"))
                        record_unfilled(c, float(pending.get(c, 0.0)), "涨停/不可买", d)
                        continue
                    buy_deltas[c] = delta

                if buy_deltas:
                    def cash_needed(s: float) -> float:
                        """按统一缩放因子 s 计算全部买入所需现金（含佣金/滑点）。"""
                        total = 0.0
                        for c, delta in buy_deltas.items():
                            sh = s * delta
                            if sh <= 1e-12:
                                continue
                            o = tradable_price[c]
                            price = o * (1.0 + slip)
                            gross = sh * price
                            fee = max(gross * comm_bps / 10_000.0, comm_min)
                            total += gross + fee
                        return total

                    # 等比例缩放：求最大 s∈[0,1] 使所需现金 <= 可用现金（预留 1e-6 缓冲，
                    # 保证成交后 cash >= -1e-6）。缩放对所有买入目标统一生效，保持目标相对权重。
                    budget = max(0.0, cash - 1e-6)
                    s = 1.0
                    if cash_needed(s) > budget:
                        lo, hi = 0.0, 1.0
                        for _ in range(80):
                            mid = 0.5 * (lo + hi)
                            if cash_needed(mid) <= budget:
                                lo = mid
                            else:
                                hi = mid
                        s = lo
                    for c, delta in buy_deltas.items():
                        sh = s * delta
                        if sh <= 1e-12:
                            continue
                        o = tradable_price[c]
                        price = o * (1.0 + slip)
                        gross = sh * price
                        fee = max(gross * comm_bps / 10_000.0, comm_min)
                        cash -= gross + fee
                        positions[c] = float(positions.get(c, 0.0)) + sh
                        lots.setdefault(c, []).append([d, sh])   # 记录买入批次（买入日 d）
                        cost["commission"] += fee
                        cost["slippage"] += sh * o * slip
                        trades.append(Trade(d, c, "BUY", sh, price, gross, fee, 0.0, sh * o * slip))

                # 清理极小仓位并同步 lots（保持 positions == Σ lot 股数）
                positions = {c: s for c, s in positions.items() if s > 1e-9}
                for c in list(lots):
                    lots[c] = [lot for lot in lots[c] if lot[1] > 1e-9]
                    if not lots[c]:
                        del lots[c]
                assert_cash(d, cash)

                union = pending.index.union(last_target.index)
                target_turnover_history[d] = float(
                    (pending.reindex(union).fillna(0.0) - last_target.reindex(union).fillna(0.0)).abs().sum() / 2.0
                )
                last_target = pending.copy()
                pending, pending_date = None, None

            # ---- 2) 收盘盯市 ----
            cl = self.close_ffill.loc[d]
            pos_value = sum(sh * float(cl.get(c, 0.0)) for c, sh in positions.items())
            pv = cash + pos_value
            equity[d] = pv
            cash_series[d] = cash
            exposure_series[d] = pos_value / pv if pv > 0 else 0.0
            assert_cash(d, cash)
            if self.on_mark:
                self.on_mark(d, pv)

            # ---- 3) 收盘生成下一交易日的目标权重 ----
            # 普通月末调仓日，或熔断敞口变化当日强制调仓（force_rebalance），
            # 均在此生成 pending，T+1 开盘执行；weights_fn(date) 支持任意交易日。
            if d in self.rebal_set or self.force_rebalance:
                w = self.weights_fn(d)
                if carry:
                    # 合并方式：被拒标的与本次新目标逐标的取权重上限(element-wise max)。
                    # 被涨跌停/停牌卡住的买入目标通常是较高权重，取上限保证其在下一个
                    # 可执行日获得重试机会；减仓类目标其目标权重低于现持仓，取上限不会
                    # 误增仓。合并后清空队列，若再次被拒会在下次执行时重新登记。
                    merged = w.copy()
                    for c, cw in carry.items():
                        merged[c] = max(float(merged.get(c, 0.0)), float(cw))
                    w = merged
                    carry.clear()
                target_weights_history[d] = w.copy()
                pending, pending_date = w, d
                self.force_rebalance = False

        eq = pd.Series(equity, name="equity")
        tdf = pd.DataFrame([t.__dict__ for t in trades])
        total_cost = float(sum(cost.values()))
        self.positions = positions
        self.lots = lots
        return BacktestResult(
            equity=eq, trades=tdf,
            weights_history=target_weights_history, turnover_history=target_turnover_history,
            target_weights_history=target_weights_history, target_turnover_history=target_turnover_history,
            cash_history=pd.Series(cash_series, name="cash"),
            exposure_history=pd.Series(exposure_series, name="exposure"),
            unfilled_history=unfilled_history,
            blocked_count=blocked_count,
            cost_total=total_cost,
            cost_breakdown={k: round(v, 2) for k, v in cost.items()},
            config_snapshot={"cost": cfg.section("cost"), "period": cfg.section("period"),
                             "strategy": cfg.section("strategy")},
        )
