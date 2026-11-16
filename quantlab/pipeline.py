"""流水线编排：把因子 → 策略 → 回测 → 风控串成一次可复现运行。"""
from __future__ import annotations

from .engine import BacktestEngine
from .factors import build_score
from .risk import DrawdownFuse
from .strategy import build_weight_function, rebalance_dates


def run_backtest(md, cfg):
    """返回 dict：result / score / factor_detail / fuse / weights_fn。"""
    score, detail = build_score(md, cfg)
    fuse = DrawdownFuse(cfg)
    wf = build_weight_function(md, cfg, score, lambda d: fuse.exposure)
    dates = rebalance_dates(md, cfg.get("period.start"), cfg.get("period.end"))
    engine = BacktestEngine(md, cfg, wf, dates)
    engine.on_mark = lambda d, eq: fuse.update(d, eq)
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
