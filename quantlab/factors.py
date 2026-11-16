"""因子层：价量因子构造、横截面预处理、IC/IR/分层检验、多重检验校正。

硬性纪律：
  * 所有因子只使用 <= T 日的信息（shift / rolling 均为向后窗口）；
  * forward_returns() 仅用于「评价」，绝不进入信号或权重；
  * EP/BP/ROE 等财务因子需要带 publish_date 的 PIT 数据，当前数据源不覆盖 → 插件留桩，不伪造。
"""
from __future__ import annotations

import math
from typing import Dict

import numpy as np
import pandas as pd

FACTOR_META: Dict[str, dict] = {
    "mom_60":    {"desc": "60日动量（跳过最近5日）", "formula": "close.shift(5)/close.shift(65)-1", "sign": +1},
    "mom_120":   {"desc": "120日动量（跳过最近5日）", "formula": "close.shift(5)/close.shift(125)-1", "sign": +1},
    "rev_5":     {"desc": "5日短期反转", "formula": "-(close/close.shift(5)-1)", "sign": +1},
    "low_vol_20": {"desc": "20日低波动", "formula": "-std(ret,20)", "sign": +1},
    "liq_20":    {"desc": "20日流动性（对数成交额）", "formula": "log(mean(amount,20))", "sign": +1},
    "illiq_20":  {"desc": "20日低价格冲击（负 Amihud）", "formula": "-mean(|ret|/amount,20)", "sign": +1},
}


def raw_factors(md, cfg) -> Dict[str, pd.DataFrame]:
    """构造原始因子面板（日期 × 代码）。全部为向后窗口，无未来函数。"""
    c, amt, ret = md.close, md.amount, md.ret
    f = {
        "mom_60": c.shift(5) / c.shift(65) - 1.0,
        "mom_120": c.shift(5) / c.shift(125) - 1.0,
        "rev_5": -(c / c.shift(5) - 1.0),
        "low_vol_20": -ret.rolling(20, min_periods=15).std(),
        "liq_20": np.log(amt.rolling(20, min_periods=15).mean().clip(lower=1.0)),
        "illiq_20": -(ret.abs() / amt.clip(lower=1.0)).rolling(20, min_periods=15).mean(),
    }
    enabled = cfg.get("factors.enabled", list(f))
    # FACTOR_META.sign 接线：预注册方向（全部 +1）在此生效。
    # 审核裁决明确「禁止据本次样本反号」，故这里只实现开关，不把 sign 改成 -1。
    return {k: f[k] * float(FACTOR_META.get(k, {}).get("sign", 1)) for k in enabled}


def winsorize_mad(df: pd.DataFrame, k: float = 3.0) -> pd.DataFrame:
    """横截面 MAD 去极值，避免市值/成交额类长尾主导排名。"""
    med = df.median(axis=1)
    mad = df.sub(med, axis=0).abs().median(axis=1) * 1.4826
    mad = mad.replace(0, np.nan)
    lo, hi = med - k * mad, med + k * mad
    return df.clip(lower=lo, upper=hi, axis=0)


def cross_section_rank_zscore(df: pd.DataFrame, min_names: int = 5) -> pd.DataFrame:
    """横截面百分位排名 → Z-score；有效标的不足则整行置 NaN。"""
    r = df.rank(axis=1, pct=True)
    z = r.sub(r.mean(axis=1), axis=0).div(r.std(axis=1).replace(0, np.nan), axis=0)
    z = z.where(df.notna())
    z.loc[r.count(axis=1) < min_names, :] = np.nan
    return z


def apply_winsor(df: pd.DataFrame, spec: str) -> pd.DataFrame:
    """按 factors.winsor 配置执行去极值；未知取值直接报错，不静默回退。"""
    s = str(spec or "mad3").strip().lower()
    if s in ("none", "off", "false"):
        return df
    if s.startswith("mad"):
        tail = s[3:].strip()
        k = float(tail) if tail else 3.0
        return winsorize_mad(df, k)
    if s.startswith("q"):
        tail = s[1:].strip().lstrip("_")
        p = float(tail) / 100.0 if tail and float(tail) > 1 else (float(tail) if tail else 0.01)
        p = min(max(p, 1e-6), 0.49)
        lo = df.quantile(p, axis=1)
        hi = df.quantile(1.0 - p, axis=1)
        return df.clip(lower=lo, upper=hi, axis=0)
    raise ValueError(f"未知的 factors.winsor 取值：{spec!r}（支持 none / mad3 / madK / q1 / q0.01）")


def apply_standardize(w: pd.DataFrame, spec: str, min_names: int) -> pd.DataFrame:
    """按 factors.standardize 配置执行横截面标准化；未知取值直接报错。"""
    s = str(spec or "rank_zscore").strip().lower()
    if s in ("none", "raw", "off"):
        return w
    if s == "rank_zscore":
        return cross_section_rank_zscore(w, min_names)
    if s == "zscore":
        return w.sub(w.mean(axis=1), axis=0).div(
            w.std(axis=1).replace(0, np.nan), axis=0).where(w.notna())
    if s in ("rank_pct", "rank"):
        return w.rank(axis=1, pct=True).where(w.notna())
    raise ValueError(f"未知的 factors.standardize 取值：{spec!r}"
                     "（支持 none / rank_zscore / zscore / rank_pct）")


def effective_min_names(cfg, declared: int) -> int:
    """按 factors.nan_policy 解析最小横截面宽度。"""
    policy = str(cfg.get("factors.nan_policy", "drop_cross_section")).strip().lower()
    if policy == "keep_partial":
        return 1
    if policy in ("drop_cross_section", "fill_zero"):
        return int(declared)
    raise ValueError(f"未知的 factors.nan_policy 取值：{policy!r}"
                     "（支持 drop_cross_section / keep_partial / fill_zero）")


def industry_neutralize(z: pd.DataFrame, industry: pd.DataFrame, enabled: bool = False) -> pd.DataFrame:
    """行业内去均值。行业数据缺失（全 UNKNOWN）时返回原值并登记缺口，不伪造行业标签。"""
    if not enabled:
        return z
    known = industry.replace("UNKNOWN", np.nan)
    if known.notna().sum().sum() == 0:
        return z
    out = z.copy()
    for d in z.index:
        groups = known.loc[d]
        for name in groups.dropna().unique():
            cols = groups[groups == name].index
            vals = out.loc[d, cols]
            out.loc[d, cols] = vals - vals.mean()
    return out


def build_score(md, cfg):
    """返回 (合成因子得分, 明细字典)。得分仅用 T 日及之前数据。

    专家团审核 followup #8：winsor / standardize / nan_policy / FACTOR_META.sign
    此前均写在代码里当常量，配置改了不生效。本版全部由 config 驱动并对非法值报错。
    """
    raws = raw_factors(md, cfg)
    declared_min = int(cfg.get("factors.min_cross_section", 5))
    min_names = effective_min_names(cfg, declared_min)
    winsor_spec = str(cfg.get("factors.winsor", "mad3"))
    std_spec = str(cfg.get("factors.standardize", "rank_zscore"))
    nan_policy = str(cfg.get("factors.nan_policy", "drop_cross_section")).strip().lower()
    neutral = cfg.get("factors.neutralize", "industry_optional") == "industry_optional" and (
        md.industry.replace("UNKNOWN", np.nan).notna().sum().sum() > 0
    )
    zs = {}
    for name, raw in raws.items():
        w = apply_winsor(raw, winsor_spec)
        z = apply_standardize(w, std_spec, min_names)
        if nan_policy == "fill_zero":
            z = z.fillna(0.0)
        zs[name] = industry_neutralize(z, md.industry, enabled=neutral)
    weights = cfg.get("factors.weights", {})
    parts = [zs[k] * float(weights.get(k, 0.0)) for k in zs]
    score = sum(parts) if parts else pd.DataFrame(0.0, index=md.close.index, columns=md.close.columns)
    score = cross_section_rank_zscore(score, declared_min)
    score = score.where(md.eligible)
    wiring = {
        "winsor": winsor_spec, "standardize": std_spec, "nan_policy": nan_policy,
        "min_cross_section": declared_min, "effective_min_names": min_names,
        "neutralize": str(cfg.get("factors.neutralize", "industry_optional")),
        "industry_neutralized": bool(neutral),
        "factor_signs": {k: float(FACTOR_META.get(k, {}).get("sign", 1)) for k in raws},
        "sign_policy": "预注册方向（全部 +1）；审核裁决禁止据本次样本反号，方向重估须作新假设在全新样本重走流程",
    }
    return score, {"raw": raws, "z": zs, "industry_neutralized": bool(neutral),
                   "wiring": wiring}


def forward_returns(md, horizon: int = 20) -> pd.DataFrame:
    """评价专用：T → T+h 的前向收益。禁止传入任何信号生成函数。"""
    return md.close.shift(-horizon) / md.close - 1.0


def compute_ic(factor: pd.DataFrame, fwd: pd.DataFrame, method: str = "spearman") -> pd.Series:
    """逐日横截面 IC。样本不足 min 5 的日期记 NaN，不插值。"""
    out = {}
    for d in factor.index:
        x, y = factor.loc[d], fwd.loc[d]
        m = x.notna() & y.notna()
        if int(m.sum()) < 5:
            out[d] = np.nan
            continue
        if method == "spearman":
            out[d] = x[m].rank().corr(y[m].rank())
        else:
            out[d] = x[m].corr(y[m])
    return pd.Series(out, name="IC")


def normal_two_sided_p(t: float) -> float:
    return math.erfc(abs(float(t)) / math.sqrt(2.0))


def _hac_t_stat(s: pd.Series, lags: int) -> float:
    """Newey-West / Bartlett 核 HAC 均值 t 值。

    IC 由 h 日重叠前向收益逐日计算，序列存在强自相关；iid 假设下的
    t = mean/(std/sqrt(n)) 会系统性放大显著性（专家团审核发现 F02）。
    lags 取重叠窗口长度，对 h=20 即 20 阶。
    """
    x = s.to_numpy(dtype=float)
    n = len(x)
    d = x - x.mean()
    L = max(1, min(int(lags), n - 1))
    var = float(np.dot(d, d) / n)
    for lag in range(1, L + 1):
        gamma = float(np.dot(d[lag:], d[:-lag]) / n)
        var += 2.0 * (1.0 - lag / (L + 1.0)) * gamma
    se = math.sqrt(max(var, 1e-18) / n)
    return float(x.mean() / se) if se > 0 else float("nan")


def ic_stats(ic: pd.Series, overlap_lags: int = 20) -> dict:
    """IC 汇总。t/p 用 HAC（重叠窗口自相关稳健），并保留 iid t 作对照。"""
    s = ic.dropna()
    if len(s) < 2:
        return {"ic_mean": None, "ic_std": None, "icir": None, "t_stat": None,
                "positive_ratio": None, "n_periods": int(len(s)), "p_value": None,
                "t_stat_iid": None, "hac_lags": int(overlap_lags), "method": "HAC(Bartlett)"}
    mean, std = float(s.mean()), float(s.std(ddof=1))
    icir = mean / std if std > 0 else float("nan")
    t_iid = mean / (std / math.sqrt(len(s))) if std > 0 else float("nan")
    t = _hac_t_stat(s, overlap_lags)
    return {
        "ic_mean": round(mean, 6), "ic_std": round(std, 6),
        "icir": round(icir, 4) if np.isfinite(icir) else None,
        "t_stat": round(t, 4) if np.isfinite(t) else None,
        "t_stat_iid": round(t_iid, 4) if np.isfinite(t_iid) else None,
        "hac_lags": int(overlap_lags),
        "method": "HAC(Bartlett)；重叠前向收益自相关稳健，iid t 另列对照",
        "p_value": round(normal_two_sided_p(t), 6) if np.isfinite(t) else None,
        "positive_ratio": round(float((s > 0).mean()), 4),
        "n_periods": int(len(s)),
    }


def layered_backtest(factor: pd.DataFrame, fwd: pd.DataFrame, n_groups: int = 5) -> dict:
    """分层回测：按因子值分 N 组等权，检验单调性。"""
    layers = {g: [] for g in range(n_groups)}
    for d in factor.index:
        x, y = factor.loc[d], fwd.loc[d]
        m = x.notna() & y.notna()
        if int(m.sum()) < n_groups * 2:
            continue
        ranks = x[m].rank(method="first")
        try:
            q = pd.qcut(ranks, n_groups, labels=False)
        except ValueError:
            continue
        for g in range(n_groups):
            vals = y[m].iloc[np.where(q == g)[0]]
            if len(vals):
                layers[g].append(float(vals.mean()))
    means = [float(np.mean(layers[g])) if layers[g] else float("nan") for g in range(n_groups)]
    clean = [(g, v) for g, v in enumerate(means) if np.isfinite(v)]
    mono = float(np.corrcoef([g for g, _ in clean], [v for _, v in clean])[0, 1]) if len(clean) > 2 else None
    return {
        "n_groups": n_groups,
        "group_mean_forward_return": [round(v, 6) if np.isfinite(v) else None for v in means],
        "long_short_spread": round(means[-1] - means[0], 6) if all(np.isfinite(v) for v in means) else None,
        "monotonicity_corr": round(mono, 4) if mono is not None else None,
        "observations_per_group": {str(g): len(layers[g]) for g in range(n_groups)},
    }


def bh_fdr(pvalues, alpha: float = 0.05, n_attempted: int | None = None) -> dict:
    """Benjamini-Hochberg FDR 校正。

    n_attempted 为实际尝试过的检验总数（含未进入最终报告的因子与参数组合）。
    专家团审核发现 F03：分母若只用最终保留的 6 个 p 值，会严重低估校正强度；
    DSR 已用 54 次试验，两套口径必须统一。
    """
    p = np.asarray([x for x in pvalues if x is not None and np.isfinite(x)], dtype=float)
    n = len(p)
    if n == 0:
        return {"n_tests": 0, "cutoff": 0.0, "n_rejected": 0, "alpha": alpha,
                "n_attempted": int(n_attempted or 0)}
    denom = max(n, int(n_attempted)) if n_attempted else n
    order = np.argsort(p)
    thresholds = alpha * np.arange(1, n + 1) / denom
    passed = p[order] <= thresholds
    k = int(np.where(passed)[0].max()) + 1 if passed.any() else 0
    cutoff = float(p[order[k - 1]]) if k else 0.0
    return {"n_tests": n, "n_attempted": denom, "cutoff": round(cutoff, 6),
            "n_rejected": int(k), "alpha": alpha,
            "method": "Benjamini-Hochberg FDR",
            "note": "阈值分母采用尝试数，而非最终保留的 p 值个数"}


def ic_decay(factor: pd.DataFrame, md, horizons=(1, 5, 10, 20, 40)) -> dict:
    out = {}
    for h in horizons:
        st = ic_stats(compute_ic(factor, forward_returns(md, h)), overlap_lags=h)
        out[str(h)] = st["ic_mean"]
    vals = [(int(h), v) for h, v in out.items() if v is not None]
    half_life = None
    if len(vals) >= 2 and vals[0][1] and vals[0][1] > 0:
        for h, v in vals[1:]:
            if v <= vals[0][1] / 2:
                half_life = h
                break
    return {"by_horizon": out, "half_life_days_estimate": half_life,
            "note": "半衰期为线性插值前的粗略估计；样本不足时可能为 None"}


def factor_correlation(zs: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    cols = {k: v.stack().dropna() for k, v in zs.items()}
    return pd.DataFrame(cols).corr().round(4)


def factor_research(md, cfg, score=None) -> dict:
    """Step 1 汇总：逐因子 IC/IR/t、分层、衰减、多重检验、因子相关性、数据陷阱登记。"""
    if score is None:
        score, detail = build_score(md, cfg)
    else:
        _, detail = build_score(md, cfg)
    zs = detail["z"]
    fwd20 = forward_returns(md, 20)
    per_factor, pvals = {}, []
    for name, z in zs.items():
        ic = compute_ic(z, fwd20)
        st = ic_stats(ic)
        st["layered"] = layered_backtest(z, fwd20, 5)
        st["decay"] = ic_decay(z, md, (1, 5, 10, 20, 40))
        per_factor[name] = st
        pvals.append(st["p_value"])
    composite = ic_stats(compute_ic(score, fwd20))
    # 试验次数由真实 sensitivity_axes 推导，不再读人工填写的常量（followup #7/#8）。
    axes = cfg.get("validation.sensitivity_axes", {}) or {}
    n_grid = 1 + sum(len([v for v in vals if v != cfg.get(axis)])
                     for axis, vals in axes.items())
    attempted = int(cfg.get("validation.trials_factor_count", len(zs))) * int(n_grid)
    fdr = bh_fdr(pvals, float(cfg.get("validation.fdr_alpha", 0.05)), n_attempted=attempted)
    sig_after = [name for name, st in per_factor.items()
                 if st["p_value"] is not None and st["p_value"] <= fdr["cutoff"]]
    return {
        "per_factor": per_factor,
        "composite_ic": composite,
        "factor_correlation": factor_correlation(zs).to_dict(),
        "multiple_testing": {
            **fdr,
            "attempted_total": attempted,
            "attempted_factors": int(cfg.get("validation.trials_factor_count", len(zs))),
            "attempted_param_combos": int(cfg.get("validation.trials_param_grid", 9)),
            "significant_before": [n for n, s in per_factor.items()
                                   if s["p_value"] is not None and s["p_value"] < 0.05],
            "significant_after_fdr": sig_after,
        },
        "config_wiring": {
            "factor_pipeline": detail["wiring"],
            "universe": getattr(md, "wiring", {}),
            "cost_impact_model": cfg.get("cost.impact_model"),
            "fundamental_plugin": cfg.get("factors.fundamental_plugin"),
            "note": "逐项声明配置项是否真正进入计算；行业上限因 industry=UNKNOWN 空转属显式登记，不伪造生效",
        },
        "data_quality": {
            "lookahead_bias": "已处理：因子仅用 shift/rolling 向后窗口；前向收益不进入信号",
            "survivorship_bias": "未完全处理：演示样本为当前存续的20只，缺退市股与成分股历史变动 → 登记为风险项",
            "suspension_limit": "已处理：停牌/涨跌停/上市不足120日在 eligible 与撮合层剔除",
            "report_lag": "不适用：本次未启用财务因子（PIT 数据缺失，插件未启用）",
            "industry_neutralized": bool(detail["industry_neutralized"]),
            "known_gaps": cfg.get("data.known_gaps", []),
        },
    }
