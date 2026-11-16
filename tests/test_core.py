"""核心自检：无未来函数 / 涨跌停 / T+1 / 成本 / 统计口径 / 真实数据跨源校验。

运行：python -m quantlab selftest
"""
from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quantlab.config import Config
from quantlab.data import MarketData, board_price_limit, load_prices
from quantlab.engine import BacktestEngine
from quantlab.factors import bh_fdr, compute_ic, ic_stats, raw_factors
from quantlab.pipeline import run_backtest
from quantlab.risk import DrawdownFuse, historical_scenarios

ROOT = Path(__file__).resolve().parent.parent
CFG = Config.load()
CODES = [f"{600100 + i:06d}" for i in range(8)]


def synth(n_days: int = 80, seed: int = 7) -> MarketData:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=n_days).strftime("%Y-%m-%d").tolist()
    rows = []
    for k, code in enumerate(CODES):
        px = 10.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n_days)) + k * 0.01)
        for i, d in enumerate(dates):
            o = float(px[i - 1]) if i > 0 else float(px[0])
            c = float(px[i])
            rows.append({"date": d, "code": code, "open": o, "high": max(o, c) * 1.01,
                         "low": min(o, c) * 0.99, "close": c, "volume": 10000.0 + k,
                         "amount": (10000.0 + k) * 100.0 * c,
                         "is_st": 0, "industry": "UNKNOWN"})
    return MarketData(pd.DataFrame(rows), CFG)


class TestNoLookahead(unittest.TestCase):
    def test_factors_use_only_past_information(self):
        md = synth(200)
        full = raw_factors(md, CFG)
        cut = md.df[md.df["date"] <= md.dates[149]]
        trunc = raw_factors(MarketData(cut, CFG), CFG)
        for name in full:
            a = full[name].loc[:md.dates[149]]
            b = trunc[name].loc[:md.dates[149]]
            self.assertTrue(np.allclose(a.fillna(0).to_numpy(), b.fillna(0).to_numpy()),
                            f"{name} 的取值被未来数据改变了")

    def test_forward_return_is_future_only(self):
        md = synth(80)
        from quantlab.factors import forward_returns
        fwd = forward_returns(md, 5)
        self.assertTrue(fwd.loc[md.dates[0]].notna().any())
        self.assertTrue(fwd.loc[md.dates[-1]].isna().all())


class TestTradingConstraints(unittest.TestCase):
    def test_board_limits(self):
        self.assertEqual(board_price_limit("600519"), 0.10)
        self.assertEqual(board_price_limit("300750"), 0.20)
        self.assertEqual(board_price_limit("688981"), 0.20)
        self.assertEqual(board_price_limit("830001"), 0.30)

    def test_limit_up_blocks_buy(self):
        md = synth(60)
        d = md.dates[10]
        code = CODES[0]
        # 把该日开盘价改成前一日收盘价的涨停价
        pc = md.close.loc[md.dates[9], code]
        md.open.loc[d, code] = round(pc * 1.10, 2)
        up = md.prev_close.mul(1.0 + md._lim, axis=1).round(2)
        md.can_buy = md.open.notna() & (md.open < up - 1e-6)
        md.can_sell = md.open.notna() & (md.open > md.prev_close.mul(1.0 - md._lim, axis=1).round(2) + 1e-6)
        self.assertFalse(bool(md.can_buy.loc[d, code]))
        w = pd.Series({code: 1.0})
        eng = BacktestEngine(md, CFG, lambda date: w, [md.dates[9]])
        res = eng.run()
        buys = res.trades[res.trades["side"] == "BUY"] if len(res.trades) else pd.DataFrame()
        self.assertEqual(len(buys[buys["code"] == code]), 0, "涨停日不应成交买入")

    def test_t_plus_1_and_cost_math(self):
        md = synth(60)
        w = pd.Series({CODES[0]: 0.5, CODES[1]: 0.5})
        eng = BacktestEngine(md, CFG, lambda date: w, [md.dates[0]])
        res = eng.run()
        buys = res.trades[res.trades["side"] == "BUY"]
        self.assertGreater(len(buys), 0)
        for _, t in buys.iterrows():
            expected = max(t["gross"] * CFG.get("cost.commission_bps") / 10_000.0,
                           CFG.get("cost.commission_min_cny"))
            self.assertAlmostEqual(t["commission"], expected, places=6)
            self.assertAlmostEqual(t["tax"], 0.0, places=6)
        self.assertTrue((res.equity > 0).all(), "净值不得为负")


class TestStatistics(unittest.TestCase):
    def test_ic_stats_shape(self):
        md = synth(150)
        from quantlab.factors import forward_returns
        f = raw_factors(md, CFG)["mom_60"]
        ic = compute_ic(f, forward_returns(md, 20))
        st = ic_stats(ic)
        self.assertIn("icir", st)
        self.assertGreater(st["n_periods"], 0)

    def test_bh_fdr(self):
        r = bh_fdr([0.001, 0.02, 0.04, 0.6, 0.9], alpha=0.05)
        self.assertEqual(r["n_tests"], 5)
        self.assertGreaterEqual(r["n_rejected"], 1)
        self.assertLessEqual(r["cutoff"], 0.05)
        empty = bh_fdr([], alpha=0.05)
        self.assertEqual(empty["n_tests"], 0)


class TestConfig(unittest.TestCase):
    def test_step0_cost_assumptions_locked(self):
        self.assertEqual(CFG.get("cost.commission_bps"), 2.5)
        self.assertEqual(CFG.get("cost.stamp_tax_sell_bps"), 10.0)
        self.assertEqual(CFG.get("cost.slippage_bps"), 5.0)
        self.assertEqual(CFG.get("cost.impact_model"), "none")
        self.assertFalse(CFG.get("leverage.enabled"))
        self.assertEqual(CFG.get("period.oos_start"), "2024-01-01")

    def test_dotted_get(self):
        self.assertEqual(CFG.get("strategy.trend.ma_long"), 120)
        self.assertIsNone(CFG.get("not.exist.key"))


class TestRealData(unittest.TestCase):
    def test_cross_source_ohlc_600519(self):
        """与通达信 MCP 在 2026-11-13 的 600519 日线逐字段交叉校验。"""
        path = ROOT / "data" / "sample_real" / "prices.csv"
        if not path.exists():
            self.skipTest("真实样本未下载：先执行 python -m quantlab fetch")
        df = load_prices(path)
        row = df[(df["code"] == "600519") & (df["date"] == "2026-11-13")]
        self.assertEqual(len(row), 1)
        r = row.iloc[0]
        self.assertAlmostEqual(float(r["open"]), 1404.00, places=2)
        self.assertAlmostEqual(float(r["close"]), 1435.26, places=2)
        self.assertAlmostEqual(float(r["high"]), 1437.69, places=2)
        self.assertAlmostEqual(float(r["low"]), 1394.00, places=2)

    def test_quick_pipeline_runs(self):
        path = ROOT / "data" / "sample_real" / "prices.csv"
        if not path.exists():
            self.skipTest("真实样本未下载")
        md = MarketData(load_prices(path), CFG)
        cfg = CFG.with_overrides(**{"period.start": "2024-01-01"})
        res = run_backtest(md, cfg)["result"]
        self.assertGreater(len(res.equity), 400)
        self.assertFalse(res.equity.isna().any())
        self.assertTrue((res.equity > 0).all())


class TestEngineCashAndExposure(unittest.TestCase):
    """引擎级缺陷回归：两段式撮合 + 买入按可用现金等比例缩放。"""

    def test_cash_nonnegative_and_exposure_le_one(self):
        md = synth(200)
        # 故意超配（Σw=1.6 > 1），强制触发买入缩放，检验现金不为负、敞口不越 1。
        w = pd.Series({c: 0.2 for c in CODES})
        reb = [md.dates[i] for i in (0, 10, 20, 30, 40, 50, 60, 70, 80, 90)]
        res = BacktestEngine(md, CFG, lambda date: w, reb).run()
        self.assertGreaterEqual(float(res.cash_history.min()), -1e-6,
                                "任意时点现金不得为负（纯多头、无杠杆）")
        self.assertLessEqual(float(res.exposure_history.max()), 1.0 + 1e-6,
                             "持仓市值/总资产不得超过 1")
        self.assertFalse(res.cash_history.isna().any())
        self.assertFalse(res.exposure_history.isna().any())
        self.assertEqual(len(res.cash_history), len(res.equity))
        self.assertEqual(len(res.exposure_history), len(res.equity))
        # 完整 pipeline 路径同样应产出这两个字段并满足约束
        pres = run_backtest(md, CFG)["result"]
        self.assertGreaterEqual(float(pres.cash_history.min()), -1e-6)
        self.assertLessEqual(float(pres.exposure_history.max()), 1.0 + 1e-6)


class TestTPlusOne(unittest.TestCase):
    """T+1 真实生效：按持仓批次 lots 记录买入日、FIFO 消耗，当日买入不可卖。"""

    def test_t_plus_1_lots_fifo(self):
        md = synth(60)
        code = CODES[0]
        d1, d2, d3 = md.dates[0], md.dates[1], md.dates[2]

        def wf(date):
            return {d1: pd.Series({code: 1.0}),     # 先买满
                    d2: pd.Series({code: 0.5}),     # 连续调仓日：减半
                    d3: pd.Series({code: 0.3})}.get(date, pd.Series(dtype=float))  # 再减仓留底仓

        eng = BacktestEngine(md, CFG, wf, [d1, d2, d3])
        res = eng.run()
        t = res.trades
        buys = t[(t["side"] == "BUY") & (t["code"] == code)]
        sells = t[(t["side"] == "SELL") & (t["code"] == code)]
        self.assertGreater(len(buys), 0, "应先有买入")
        self.assertGreater(len(sells), 0, "连续减仓应产生卖出")
        # 当日买入部分不可卖：同标的买卖不得落在同一交易日
        self.assertFalse(set(buys["date"]) & set(sells["date"]),
                         "T+1 违规：当日买入的份额被当日卖出")
        first_buy = min(buys["date"])
        for sd in sells["date"]:
            self.assertGreater(sd, first_buy, "卖出必须发生在买入之后的交易日")
        self.assertLessEqual(float(sells["shares"].sum()), float(buys["shares"].sum()) + 1e-6)
        # lots 账本真实登记（不是 dead code）且与持仓自洽（FIFO 部分消耗）
        self.assertIn(code, eng.lots, "买入批次 lots 应被登记")
        for c, ls in eng.lots.items():
            self.assertAlmostEqual(sum(sh for _, sh in ls),
                                   float(eng.positions.get(c, 0.0)), places=6)


class TestDrawdownFuseStateMachine(unittest.TestCase):
    """熔断状态机：任意档位逐级升档到清仓档、清仓档可经影子净值恢复。"""

    def test_stepwise_escalation_to_clearing_and_recovery(self):
        fuse = DrawdownFuse(CFG)
        fuse.update("2020-01-01", 1_000_000.0, 0.0)
        self.assertEqual(fuse.exposure, 1.0)
        fuse.update("2020-01-02", 840_000.0, -0.16)   # 真实回撤 0.16
        self.assertEqual(fuse.exposure, 0.5)
        fuse.update("2020-01-03", 780_000.0, -0.07)   # 真实回撤 0.22
        self.assertEqual(fuse.exposure, 0.0, "应能从任意档位逐级升到 0.0 清仓档")

        # 清仓后组合为现金、净值冻结，但市场回升 → 影子净值回撤收敛 → 恢复
        dates = pd.bdate_range("2020-02-03", periods=60).strftime("%Y-%m-%d").tolist()
        recovered = False
        for dt in dates:
            fuse.update(dt, 780_000.0, 0.02)
            if fuse.exposure == 1.0:
                recovered = True
                break
        self.assertTrue(recovered, "清仓档应存在恢复路径（影子净值）")
        self.assertEqual(fuse.exposure, 1.0)
        # 恢复后不应被冻结的真实 dd 立刻误升档
        fuse.update("2020-06-01", 780_000.0, 0.0)
        self.assertEqual(fuse.exposure, 1.0)
        # 事件结构向后兼容（date/drawdown/action）且新增 trigger_date / effective_date
        self.assertTrue(all({"date", "drawdown", "action"} <= set(e) for e in fuse.events))
        self.assertTrue(all("trigger_date" in e and "effective_date" in e for e in fuse.events))


class TestStressGlobalPeak(unittest.TestCase):
    """历史情景：全局峰值口径，窗口内不得用 seg.cummax() 重置峰值。"""

    def test_historical_scenarios_uses_global_peak(self):
        idx = pd.bdate_range("2020-01-01", periods=300).strftime("%Y-%m-%d").tolist()
        # 峰值 150 出现在窗口外（第 2 个交易日），窗口内净值恒定 100
        vals = [100.0, 150.0] + [100.0] * (len(idx) - 2)
        eq = pd.Series(vals, index=idx)
        a, b = idx[100], idx[200]
        out = historical_scenarios(eq, {"W": (a, b)})
        self.assertTrue(out["W"]["covered"])
        # 旧口径窗口内 seg.cummax()=100 → 回撤 0；全局口径以窗口外 150 为基准 → 约 -0.3333
        self.assertLess(out["W"]["max_drawdown"], -0.30,
                        "全局峰值口径不应在窗口内被重置")


class TestEquivalentRandomControl(unittest.TestCase):
    """等价随机对照的核心断言：override 路径必须与基准逐值一致。"""

    def test_score_override_reproduces_baseline(self):
        md = synth(200)
        cfg = CFG.with_overrides(**{"period.start": "2022-01-01"})
        base = run_backtest(md, cfg)
        same = run_backtest(md, cfg, score_override=base["score"])
        self.assertTrue(np.allclose(base["result"].equity.to_numpy(),
                                    same["result"].equity.to_numpy(), rtol=0, atol=1e-6),
                        "真实 score 经 override 重跑必须与基准净值一致，否则对照不等价")
        self.assertAlmostEqual(base["result"].cost_total, same["result"].cost_total, places=6)

    def test_random_score_same_mask_reproducible(self):
        from quantlab.pipeline import build_random_score
        md = synth(200)  # mom_120 需要 125 个交易日，短样本会让合成得分整行 NaN
        score, _ = __import__("quantlab.factors", fromlist=["build_score"]).build_score(md, CFG)
        self.assertGreater(int(score.notna().to_numpy().sum()), 0, "合成得分不应全为 NaN")
        a = build_random_score(score, seed=42)
        b = build_random_score(score, seed=42)
        c = build_random_score(score, seed=43)
        self.assertTrue(a.notna().equals(score.notna()), "随机得分必须复制真实 NaN 掩码")
        self.assertTrue(np.allclose(a.fillna(0).to_numpy(), b.fillna(0).to_numpy()))
        self.assertFalse(np.allclose(a.fillna(0).to_numpy(), c.fillna(0).to_numpy()))


class TestPreregistrationState(unittest.TestCase):
    """样本外使用次数：指纹变化自动递增，预注册冻结，超上限降级。"""

    def test_sync_and_force(self):
        import tempfile
        from quantlab import state as rs
        with tempfile.TemporaryDirectory() as td:
            s1 = rs.sync_run(td, CFG)
            self.assertEqual(s1["oos_use_count"], 1)
            self.assertEqual(s1["event"], "baseline_initialized")
            s2 = rs.sync_run(td, CFG)
            self.assertEqual(s2["oos_use_count"], 1)
            self.assertEqual(s2["event"], "unchanged")
            changed = CFG.with_overrides(**{"strategy.top_n": 12})
            s3 = rs.sync_run(td, changed)
            self.assertEqual(s3["oos_use_count"], 2)
            self.assertEqual(s3["event"], "strategy_params_changed")
            st = rs.load_state(Path(td) / "state" / "research_state.json")
            self.assertEqual(len(st["history"]), 1)
            blocked = rs.register(td, CFG, "新假设")
            self.assertFalse(blocked["ok"])
            forced = rs.register(td, CFG, "新假设", force=True)
            self.assertTrue(forced["ok"])
            s4 = rs.sync_run(td, changed)
            self.assertEqual(s4["oos_use_count"], 3)
            degraded = rs.sync_run(td, CFG.with_overrides(**{"strategy.top_n": 5}))
            self.assertTrue(degraded["degraded"])


class TestConfigWiring(unittest.TestCase):
    """配置项接线：非法值报错、开关真生效、缺口显式登记。"""

    def test_winsor_and_standardize_specs(self):
        from quantlab.factors import apply_standardize, apply_winsor
        md = synth(120)
        raw = raw_factors(md, CFG)["mom_60"]
        self.assertTrue(apply_winsor(raw, "none").equals(raw))
        self.assertFalse(apply_winsor(raw, "mad3").fillna(0).equals(raw.fillna(0)))
        with self.assertRaises(ValueError):
            apply_winsor(raw, "bogus")
        a = apply_standardize(raw, "rank_zscore", 5)
        b = apply_standardize(raw, "zscore", 5)
        self.assertFalse(a.fillna(0).equals(b.fillna(0)))
        with self.assertRaises(ValueError):
            apply_standardize(raw, "bogus", 5)

    def test_market_proxy_rejects_unknown(self):
        from quantlab.strategy import market_proxy
        md = synth(80)
        cfg = CFG.with_overrides(**{"strategy.market_timing.proxy": "csi_all_share"})
        with self.assertRaises(ValueError):
            market_proxy(md, cfg)

    def test_min_avg_amount_bound(self):
        md0 = synth(120)
        cfg = CFG.with_overrides(**{"universe.min_avg_amount_20d": 1e12})
        md1 = MarketData(md0.df, cfg)
        self.assertTrue(md1.wiring["min_avg_amount_20d_applied"])
        self.assertLess(int(md1.eligible.to_numpy().sum()), int(md0.eligible.to_numpy().sum()))

    def test_industry_cap_applied_only_with_labels(self):
        from quantlab.strategy import _cap_industry_weights
        w = pd.Series({"a": 0.4, "b": 0.3, "c": 0.3})
        unknown = pd.Series({"a": "UNKNOWN", "b": "UNKNOWN", "c": "UNKNOWN"})
        out0, applied0 = _cap_industry_weights(w, unknown, 0.4)
        self.assertFalse(applied0)
        self.assertTrue(out0.equals(w))
        known = pd.Series({"a": "银行", "b": "银行", "c": "白酒"})
        out1, applied1 = _cap_industry_weights(w, known, 0.4)
        self.assertTrue(applied1)
        self.assertLessEqual(float(out1.loc[["a", "b"]].sum()), 0.4 + 1e-3)


class TestRiskExtensions(unittest.TestCase):
    """beta / 容量：null 显式 not_evaluated，给值即算。"""

    def test_capacity_two_paths(self):
        from quantlab.risk import beta_report, capacity_report
        md = synth(200)
        res = run_backtest(md, CFG)["result"]
        self.assertEqual(capacity_report(res, md, CFG)["status"], "not_evaluated")
        cfg = CFG.with_overrides(**{"step0.capital": 10_000_000})
        cap = capacity_report(res, md, cfg)
        self.assertEqual(cap["status"], "evaluated")
        self.assertGreater(cap["capacity_upper_bound_cny"], 0)
        self.assertTrue(np.isfinite(beta_report(res, md)["beta"]))


class TestDualPriceBasis(unittest.TestCase):
    """真实样本用未复权涨跌停基准；覆盖不足时回退且不阻断成交。"""

    def test_synthetic_falls_back(self):
        md = synth(60)
        self.assertFalse(md.raw_prices_loaded)
        self.assertIn("qfq_only", md.wiring["price_basis"])
        self.assertTrue(bool(md.can_buy.to_numpy().any()), "回退后仍应存在可买标的")

    def test_real_sample_uses_raw_limits(self):
        path = ROOT / "data" / "sample_real" / "prices.csv"
        if not (ROOT / "data" / "sample_real" / "prices_raw.csv").exists():
            self.skipTest("未复权样本未下载")
        md = MarketData(load_prices(path), CFG)
        self.assertTrue(md.raw_prices_loaded)
        self.assertTrue(md.wiring["price_basis"].startswith("raw_for_limits"))
        code, cutoff = "600519", "2024-06-03"
        date = [x for x in md.dates if x <= cutoff][-1]
        raw_prev = float(md.prev_close_raw.loc[date, code])
        qfq_prev = float(md.prev_close.loc[date, code])
        self.assertAlmostEqual(float(md.limit_up.loc[date, code]), round(raw_prev * 1.10, 2), places=2)
        self.assertNotAlmostEqual(raw_prev, qfq_prev, places=2,
                                  msg="600519 有分红除权，前复权与未复权前收应不同")


if __name__ == "__main__":
    unittest.main(verbosity=2)
