"""流水线编排：把因子 → 策略 → 回测 → 风控串成一次可复现运行。"""
from __future__ import annotations

from .engine import BacktestEngine
from .factors import build_score
from .risk import DrawdownFuse
from .strategy import build_weight_function, rebalance_dates


def run_backtest(md, cfg):
    """返回 dict：result / score / factor_detail / fuse / weights_fn。

    熔断即时生效：DrawdownFuse.exposure 发生变化的**当日**置位
    engine.force_rebalance，引擎在该日收盘（step 3）强制调用 weights_fn(date)
    生成一次新目标 pending，T+1 开盘执行——不再等到月末调仓日（最长滞后约 20 个
    交易日）。weights_fn(date) 基于 score/eligible/trend/mexpo 的 T 日及之前数据，
    支持任意交易日、无未来函数；market_return 取等权样本指数当日收益，收盘可知。
    """
    score, detail = build_score(md, cfg)
    fuse = DrawdownFuse(cfg)
    wf = build_weight_function(md, cfg, score, lambda d: fuse.exposure)
    dates = rebalance_dates(md, cfg.get("period.start"), cfg.get("period.end"))
    engine = BacktestEngine(md, cfg, wf, dates)
    # 市场（等权样本指数）日收益，供影子净值判断清仓档恢复；收盘可知，无未来函数。
    mkt_ret = md.ret.mean(axis=1).fillna(0.0)

    def on_mark(d, eq):
        before = fuse.exposure
        fuse.update(d, eq, float(mkt_ret.get(d, 0.0)))
        if fuse.exposure != before:
            # 敞口档位变化 → 当日强制生成新目标，T+1 开盘撮合（避免滞后到月末）
            engine.force_rebalance = True

    engine.on_mark = on_mark
    result = engine.run()
    result.config_snapshot["fuse_events"] = fuse.events
    return {"result": result, "score": score, "factor_detail": detail,
            "fuse": fuse, "weights_fn": wf, "rebalance_dates": dates}


def run_backtest_with_random_weights(md, cfg, seed: int = 0):
    """蒙特卡洛对照组：随机等权选股，其余约束与基准完全一致。"""
    import numpy as np
    import pandas as pd
    from .engine import BacktestEngine
    from .strategy import rebalance_dates

    rng = np.random.default_rng(seed)
    top_n = int(cfg.get("strategy.top_n", 8))
    elig = md.eligible
    state = {"last": pd.Series(dtype=float)}

    def random_wf(date: str) -> pd.Series:
        if date not in elig.index:
            return state["last"]
        pool = elig.loc[date][elig.loc[date]].index.tolist()
        if not pool:
            return state["last"]
        picks = rng.choice(pool, size=min(top_n, len(pool)), replace=False)
        w = pd.Series(1.0 / len(picks), index=picks, dtype=float)
        state["last"] = w
        return w

    dates = rebalance_dates(md, cfg.get("period.start"), cfg.get("period.end"))
    engine = BacktestEngine(md, cfg, random_wf, dates)
    return engine.run()
