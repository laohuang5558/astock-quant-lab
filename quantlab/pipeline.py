"""流水线编排：把因子 → 策略 → 回测 → 风控串成一次可复现运行。"""
from __future__ import annotations

from .engine import BacktestEngine
from .factors import build_score
from .risk import DrawdownFuse
from .strategy import build_weight_function, rebalance_dates


def run_backtest(md, cfg, score_override=None):
    """返回 dict：result / score / factor_detail / fuse / weights_fn。

    熔断即时生效：DrawdownFuse.exposure 发生变化的**当日**置位
    engine.force_rebalance，引擎在该日收盘（step 3）强制调用 weights_fn(date)
    生成一次新目标 pending，T+1 开盘执行——不再等到月末调仓日（最长滞后约 20 个
    交易日）。weights_fn(date) 基于 score/eligible/trend/mexpo 的 T 日及之前数据，
    支持任意交易日、无未来函数；market_return 取等权样本指数当日收益，收盘可知。

    score_override：等价随机对照专用。传入一张与真实 score 同形状、同 NaN 结构的
    得分面板时，跳过因子计算，但**完整保留** build_weight_function（buffer / 换手 /
    单标的与行业上限 / 趋势过滤 / MA200 择时）、DrawdownFuse、on_mark 强制调仓与
    BacktestEngine 两段式撮合。唯一差异是「选股打分值」——这是等价对照的可证基础。
    """
    if score_override is None:
        score, detail = build_score(md, cfg)
    else:
        score, detail = score_override, {}
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


def build_random_score(score: "pd.DataFrame", seed: int):
    """构造与真实 score 同 index/columns、同 NaN 结构的随机得分面板。

    随机值经同一套横截面 rank→Z-score 标准化，保证分布形状与基准可比；
    NaN 掩码逐日复制，使「可选标的集合」完全一致（否则对照会混入不可交易标的）。
    """
    import numpy as np
    import pandas as pd
    from .factors import cross_section_rank_zscore

    rng = np.random.default_rng(seed)
    noise = pd.DataFrame(rng.random(score.shape), index=score.index, columns=score.columns)
    noise = noise.where(score.notna())
    z = cross_section_rank_zscore(noise, min_names=1)
    return z.where(score.notna())


def run_backtest_with_random_weights(md, cfg, seed: int = 0, score_template=None):
    """等价随机对照：只换选股打分，其余全部复用 run_backtest 管线。

    保留此函数以兼容旧调用方；内部不再自建简化 weights_fn / 简化引擎。
    """
    if score_template is None:
        score_template, _ = build_score(md, cfg)
    rnd = build_random_score(score_template, seed)
    return run_backtest(md, cfg, score_override=rnd)["result"]
