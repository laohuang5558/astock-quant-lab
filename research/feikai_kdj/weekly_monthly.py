#!/usr/bin/env python3
"""把对话里新增的三个条件形式化：月线死叉 / 回踩 10 周线 / 震荡区间。

补充的原始对话（2026-11-17 12:28~12:31）：
    「月线死叉了」「回踩5周线了昨天」「10周线」（纠正）「震荡区间有点小」
    「我等10周线T一波」

v1 只形式化了「上证新低买 + 反弹 T + 个股 KDJ J 负 + 2 负反弹」。
本模块补齐大周期与位置条件，并做客观状态盘点。核心纪律：

  * 无前视：日频映射的周线均线 = 最近 (k-1) 根「已完成周」收盘 + 当日收盘
            （当作未完成周），绝不使用当周后续交易日的数据；
            月线状态只用「上一个已完成月」的读数。
  * 歧义不猜：「月线死叉」同时给 MACD(12,26,9) / 月线 KDJ / 月线 MA5-MA10
            三种口径，主口径显式登记，其余并列。
  * 只做统计：所有输出是历史条件频率与事实读数，不含买卖点、目标价或操作建议。

数据口径沿用 v1：前复权 qfq 用于价格与收益，未复权 raw 用于盘面读数对照，
上证指数用于择时；盘中未收盘 bar 一律排除出回测样本。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

# 「回踩」容差：当日最低价触及均线 ±tol 且收盘未跌破均线 tol 以上
TOUCH_TOL = 0.015
# 月线 MACD 参数
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9


def load_daily(as_of: str | None = None) -> tuple[pd.DataFrame, dict]:
    """读取三套行情并截断到最后完整交易日；返回日频面板与 meta。"""
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    if as_of is None:
        as_of = meta["last_complete_day"]

    q = pd.read_csv(DATA / "feikai_qfq.csv").set_index("date").sort_index()
    r = pd.read_csv(DATA / "feikai_raw.csv").set_index("date").sort_index()
    x = pd.read_csv(DATA / "index_sh000001.csv").set_index("date").sort_index()
    dates = q.index[q.index <= as_of]

    d = pd.DataFrame(index=dates)
    d["o"] = q["open"].reindex(dates).astype(float)
    d["h"] = q["high"].reindex(dates).astype(float)
    d["l"] = q["low"].reindex(dates).astype(float)
    d["c"] = q["close"].reindex(dates).astype(float)
    d["ro"] = r["open"].reindex(dates).astype(float)
    d["rh"] = r["high"].reindex(dates).astype(float)
    d["rl"] = r["low"].reindex(dates).astype(float)
    d["rc"] = r["close"].reindex(dates).astype(float)
    d["ic"] = x["close"].reindex(dates).astype(float)
    return d, meta


def _iso_keys(dates: pd.Index) -> tuple[np.ndarray, np.ndarray]:
    """返回 (连续周编号 0..n-1, ISO 年月编号)。"""
    dt = pd.to_datetime(dates)
    iso = dt.isocalendar()
    wk = iso["year"].to_numpy(int) * 100 + iso["week"].to_numpy(int)
    mo = np.array([t.year * 12 + t.month for t in dt], dtype=int)
    wchange = np.r_[True, wk[1:] != wk[:-1]]
    wid = np.cumsum(wchange) - 1
    return wid, mo


def weekly_ma(d: pd.DataFrame, k: int, price_col: str = "c") -> pd.Series:
    """日频映射的 k 周均线（行情软件口径，严格无前视）。

    对交易日 i：取 i 所在周之前最近 (k-1) 根已完成周的收盘，加上 i 当日收盘
    作为「未完成周」的最新值，共 k 项求均值。当周后续交易日不会被用到。
    """
    wid, _ = _iso_keys(d.index)
    px = d[price_col].to_numpy(float)
    # 每根完整周的收盘（组内最后一根日线的收盘）
    last_close: dict[int, float] = {}
    for w, v in zip(wid, px):
        last_close[int(w)] = float(v)  # 顺序遍历，最后写入即为该周收盘
    out = np.full(len(d), np.nan)
    for i, w in enumerate(wid):
        prev = [last_close[j] for j in range(int(w) - 1, max(int(w) - k, -1), -1)
                if j in last_close]
        if len(prev) == k - 1 and np.isfinite(px[i]):
            out[i] = (sum(prev) + float(px[i])) / k
    return pd.Series(out, index=d.index, name=f"ma{k}w")


def monthly_bars(d: pd.DataFrame) -> pd.DataFrame:
    """月线 OHLC（每月最后一根已实现日线）；最后一个月可能是未完成月。"""
    _, mo = _iso_keys(d.index)
    df = d.copy()
    df["_mo"] = mo
    g = df.groupby("_mo", sort=True)
    m = g.agg(o=("o", "first"), h=("h", "max"), l=("l", "min"),
              c=("c", "last"), n=("c", "size"))
    m["last_date"] = g.apply(lambda t: str(t.index[-1]), include_groups=False)
    return m


def sma_kdj_from_module(high, low, close, n=9, m1=3, m2=3, initial=50.0):
    """复用 v1 的 KDJ 递推（避免两套口径不一致）。"""
    import sys
    sys.path.insert(0, str(HERE))
    import strategy as S  # noqa: E402
    return S.kdj(pd.Series(high), pd.Series(low), pd.Series(close), n, m1, m2, initial)


def monthly_regime(d: pd.DataFrame) -> dict:
    """月线死叉的三种口径 + 无前视日频映射。

    confirmed：日 i 使用「上一个已完成月」的状态（回测过滤只能用这个）。
    provisional：含当前未完成月的最新状态（仅用于当前状态盘点，明确标注会变）。
    """
    m = monthly_bars(d)
    c = m["c"].astype(float)

    ema_f = c.ewm(span=MACD_FAST, adjust=False).mean()
    ema_s = c.ewm(span=MACD_SLOW, adjust=False).mean()
    dif = ema_f - ema_s
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    macd_state = (dif < dea)
    macd_event = macd_state & ~macd_state.shift(1, fill_value=False)

    j, k, dd = sma_kdj_from_module(m["h"].to_numpy(float), m["l"].to_numpy(float),
                                   c.to_numpy(float))
    kdj_state = pd.Series(k < dd, index=m.index)
    ma5 = c.rolling(5, min_periods=5).mean()
    ma10 = c.rolling(10, min_periods=10).mean()
    ma_state = (ma5 < ma10) & ma10.notna()

    _, mo = _iso_keys(d.index)
    mids = list(m.index)

    def map_confirmed(state: pd.Series) -> pd.Series:
        """对日 i 取「严格早于 i 所在月」的最后一个月状态。"""
        out = np.zeros(len(d), dtype=bool)
        valid = np.zeros(len(d), dtype=bool)
        prev: dict[int, bool] = {}
        for mid, sv in state.items():
            prev[int(mid)] = bool(sv)
        for i, cur in enumerate(mo):
            earlier = [j for j in mids if j < cur]
            if earlier:
                out[i] = prev[int(earlier[-1])]
                valid[i] = True
        return pd.Series(out, index=d.index), pd.Series(valid, index=d.index)

    states = {
        "macd": macd_state,
        "kdj": kdj_state,
        "ma5_ma10": ma_state,
    }
    mapped, validity = {}, None
    for name, st in states.items():
        s, v = map_confirmed(st)
        mapped[name] = s
        validity = v
    penultimate = {name: bool(st.iloc[-2]) if len(st) > 1 else None
                   for name, st in states.items()}
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    last_date = str(m["last_date"].iloc[-1])
    pending = [x for x in meta.get("intraday_bars", []) if x > last_date]
    latest = {
        "month_label": last_date,
        "is_complete_month": (False if pending else None),
        "month_completeness_basis": (
            f"存在更晚的盘中 bar {pending}，当月必然未走完" if pending
            else "无更晚盘中 bar 可证明未完成；是否走完需交易日历，故记为 unknown"),
        "n_bars_in_month": int(m["n"].iloc[-1]),
        "macd_dif": round(float(dif.iloc[-1]), 4),
        "macd_dea": round(float(dea.iloc[-1]), 4),
        "macd_hist": round(float(2 * (dif.iloc[-1] - dea.iloc[-1])), 4),
        "macd_deathcross": bool(macd_state.iloc[-1]),
        "macd_cross_event_this_month": bool(macd_event.iloc[-1]),
        "prev_month_macd_deathcross": bool(macd_state.iloc[-2]) if len(macd_state) > 1 else None,
        "kdj_k": round(float(k[-1]), 4), "kdj_d": round(float(dd[-1]), 4),
        "kdj_j": round(float(j[-1]), 4), "kdj_deathcross": bool(kdj_state.iloc[-1]),
        "ma5": round(float(ma5.iloc[-1]), 4) if np.isfinite(ma5.iloc[-1]) else None,
        "ma10": round(float(ma10.iloc[-1]), 4) if np.isfinite(ma10.iloc[-1]) else None,
        "ma_deathcross": bool(ma_state.iloc[-1]),
        "penultimate_completed_states": penultimate,
        "n_months": int(len(m)),
        "note": "最后一个月若未走完，月线读数会随后续行情变化；不得当作已确认信号。",
    }
    frame = pd.DataFrame(mapped, index=d.index)
    frame["valid"] = validity
    frame["monthly_macd_dif"] = np.nan
    # 仅用于展示：上一个已完成月的 DIF/DEA（confirmed 口径）
    dif_map = {int(mid): float(v) for mid, v in dif.items()}
    dea_map = {int(mid): float(v) for mid, v in dea.items()}
    d_vals, e_vals = [], []
    for cur in mo:
        earlier = [j for j in mids if j < cur]
        d_vals.append(dif_map[int(earlier[-1])] if earlier else np.nan)
        e_vals.append(dea_map[int(earlier[-1])] if earlier else np.nan)
    frame["confirmed_dif"] = d_vals
    frame["confirmed_dea"] = e_vals
    return {"frame": frame, "latest": latest, "bars": m,
            "primary": "macd", "definitions": {
                "macd": f"月线 MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL})：DIF<DEA（EMA adjust=False，初值取首月收盘）",
                "kdj": "月线 KDJ(9,3,3)：K<D",
                "ma5_ma10": "月线 MA5<MA10",
            }}


def range_metrics(d: pd.DataFrame, windows: tuple[int, ...] = (5, 10, 20, 60)) -> pd.DataFrame:
    """震荡区间宽度：(N 日最高 - N 日最低)/N 日最低，及其全样本分位。

    「震荡区间有点小」没有说明尺度，故 5/10/20/60 日四个窗口并列输出；
    20 日为主口径（同时保留 range_width / range_pct_rank 别名供事件统计使用）。
    """
    cols: dict[str, pd.Series] = {}
    for w in windows:
        hh = d["h"].rolling(w, min_periods=w).max()
        ll = d["l"].rolling(w, min_periods=w).min()
        width = (hh - ll) / ll
        cols[f"range{w}_width"] = width
        cols[f"range{w}_pct_rank"] = width.rank(pct=True)
        cols[f"range{w}_high"] = hh
        cols[f"range{w}_low"] = ll
    atr = (d["h"] - d["l"]).rolling(20, min_periods=20).mean() / d["c"]
    cols["atr_pct"] = atr
    cols["atr_pct_rank"] = atr.rank(pct=True)
    cols["range_width"] = cols["range20_width"]
    cols["range_pct_rank"] = cols["range20_pct_rank"]
    cols["hh"] = cols["range20_high"]
    cols["ll"] = cols["range20_low"]
    return pd.DataFrame(cols, index=d.index)


def touch_metrics(d: pd.DataFrame, tol: float = TOUCH_TOL) -> pd.DataFrame:
    """回踩判定：当日最低触及 k 周均线附近，且收盘未显著跌破。"""
    m5 = weekly_ma(d, 5)
    m10 = weekly_ma(d, 10)
    m5r = weekly_ma(d, 5, "rc")
    m10r = weekly_ma(d, 10, "rc")
    out = pd.DataFrame({"ma5w": m5, "ma10w": m10,
                        "ma5w_raw": m5r, "ma10w_raw": m10r}, index=d.index)
    out["dist10"] = d["c"] / m10 - 1.0
    out["dist5"] = d["c"] / m5 - 1.0
    out["low_dist10"] = d["l"] / m10 - 1.0
    out["low_dist5"] = d["l"] / m5 - 1.0
    out["touch10"] = (out["low_dist10"] <= tol) & (d["c"] >= m10 * (1.0 - tol))
    out["touch5"] = (out["low_dist5"] <= tol) & (d["c"] >= m5 * (1.0 - tol))
    out["near10"] = out["dist10"].abs() <= tol
    out["above10"] = d["c"] > m10
    return out


def current_snapshot(d: pd.DataFrame, meta: dict, reg: dict,
                     tm: pd.DataFrame, rm: pd.DataFrame) -> dict:
    """截至最后完整交易日的事实读数（含对话里三条说法的客观核对）。"""
    last = str(d.index[-1])
    prev = str(d.index[-2])
    q = pd.read_csv(DATA / "feikai_raw.csv").set_index("date").sort_index()
    xi = pd.read_csv(DATA / "index_sh000001.csv").set_index("date").sort_index()
    intraday = {}
    for key, t in (("index", xi), ("stock", q)):
        if "2026-11-17" in t.index:
            row = t.loc["2026-11-17"]
            base = t.loc[last]["close"] if last in t.index else np.nan
            intraday[key] = {
                "date": "2026-11-17", "open": round(float(row["open"]), 4),
                "high": round(float(row["high"]), 4), "low": round(float(row["low"]), 4),
                "last": round(float(row["close"]), 4),
                "pct_vs_prev_close": (round(float(row["close"] / base - 1.0) * 100, 4)
                                      if np.isfinite(base) and base > 0 else None),
                "note": "盘中未收盘快照，仅用于对照对话，未进入任何回测或统计",
            }

    def rd(x):
        return None if x is None or not np.isfinite(float(x)) else round(float(x), 6)

    last_t = tm.loc[last]
    last_r = rm.loc[last]
    # 「昨天回踩 5 周线 / 韩老师纠正 10 周线」：比较最低价到两条线的距离
    l5 = float(last_t["low_dist5"]) if np.isfinite(last_t["low_dist5"]) else None
    l10 = float(last_t["low_dist10"]) if np.isfinite(last_t["low_dist10"]) else None
    closer = None
    if l5 is not None and l10 is not None:
        closer = "ma5w" if abs(l5) < abs(l10) else ("ma10w" if abs(l10) < abs(l5) else "same")
    return {
        "last_complete_day": last,
        "prev_complete_day": prev,
        "stock_close_qfq": rd(d["c"].loc[last]),
        "stock_close_raw": rd(d["rc"].loc[last]),
        "stock_low_qfq": rd(d["l"].loc[last]),
        "stock_low_raw": rd(d["rl"].loc[last]),
        "index_close": rd(d["ic"].loc[last]),
        "ma5w_qfq": rd(last_t["ma5w"]), "ma10w_qfq": rd(last_t["ma10w"]),
        "ma5w_raw": rd(last_t["ma5w_raw"]), "ma10w_raw": rd(last_t["ma10w_raw"]),
        "dist_close_to_ma5w": rd(last_t["dist5"]),
        "dist_close_to_ma10w": rd(last_t["dist10"]),
        "dist_low_to_ma5w": rd(l5), "dist_low_to_ma10w": rd(l10),
        "closer_weekly_ma_by_low": closer,
        "touch10_today": bool(last_t["touch10"]),
        "touch5_today": bool(last_t["touch5"]),
        "near10_today": bool(last_t["near10"]),
        "above_ma10w": bool(last_t["above10"]),
        "gap_up_to_ma10w_pct": rd((last_t["ma10w"] / d["c"].loc[last] - 1.0) * 100),
        "range20_width": rd(last_r["range_width"]),
        "range20_percentile": rd(last_r["range_pct_rank"]),
        "atr20_pct": rd(last_r["atr_pct"]),
        "atr20_percentile": rd(last_r["atr_pct_rank"]),
        "range20_high": rd(last_r["hh"]), "range20_low": rd(last_r["ll"]),
        "range_by_window": {
            str(w): {
                "width": rd(last_r[f"range{w}_width"]),
                "percentile": rd(last_r[f"range{w}_pct_rank"]),
                "high": rd(last_r[f"range{w}_high"]),
                "low": rd(last_r[f"range{w}_low"]),
            } for w in (5, 10, 20, 60)
        },
        "monthly": reg["latest"],
        "monthly_confirmed_state_macd": bool(reg["frame"]["macd"].loc[last]),
        "monthly_confirmed_state_kdj": bool(reg["frame"]["kdj"].loc[last]),
        "monthly_confirmed_state_ma": bool(reg["frame"]["ma5_ma10"].loc[last]),
        "intraday_snapshot": intraday,
        "tol": TOUCH_TOL,
        "price_basis": {
            "weekly_ma_primary": "前复权 qfq（行情软件常见默认口径）",
            "weekly_ma_secondary": "未复权 raw（并列登记，除权后两条线会分叉）",
        },
        "note": "全部为截至最后完整交易日的事实读数；周线均线按无前视口径计算；不构成买卖点或操作建议。",
    }


def event_study(d: pd.DataFrame, tm: pd.DataFrame, rm: pd.DataFrame,
                horizons: tuple[int, ...] = (1, 3, 5, 10)) -> dict:
    """事件级统计：回踩 10 周线 / 月线死叉状态 / 窄幅区间下的后续个股收益。

    这里不用完整交易回测，避免主规则叠加过滤后样本坍缩到 0 笔而什么都说不了。
    """
    c = d["c"]
    fwd = {h: c.shift(-h) / c - 1.0 for h in horizons}
    base = {h: float(v.dropna().mean()) for h, v in fwd.items()}
    valid = tm["ma10w"].notna() & rm["range_pct_rank"].notna()

    def summarize(mask: pd.Series) -> dict:
        rows = {}
        for h, v in fwd.items():
            a = v[mask & valid].dropna()
            if len(a) == 0:
                rows[str(h)] = {"n": 0}
                continue
            rows[str(h)] = {
                "n": int(len(a)),
                "up_ratio": round(float((a > 0).mean()), 6),
                "mean": round(float(a.mean()), 6),
                "median": round(float(a.median()), 6),
                "excess_vs_all": round(float(a.mean() - base[h]), 6),
            }
        return {"n_events": int((mask & valid).sum()), "horizons": rows}

    narrow = rm["range_pct_rank"] <= 0.30
    wide = rm["range_pct_rank"] >= 0.70
    death = tm["macd_confirmed"] if "macd_confirmed" in tm.columns else pd.Series(False, index=d.index)
    groups = {
        "回踩 10 周线（全部）": tm["touch10"],
        "回踩 5 周线（全部）": tm["touch5"],
        "回踩 10 周线 + 月线死叉状态": tm["touch10"] & death,
        "回踩 10 周线 + 非死叉": tm["touch10"] & ~death,
        "回踩 10 周线 + 窄幅区间（分位<=30%）": tm["touch10"] & narrow,
        "回踩 10 周线 + 宽幅区间（分位>=70%）": tm["touch10"] & wide,
        "窄幅区间（分位<=30%，不看回踩）": narrow,
        "全样本基线（无条件）": pd.Series(True, index=d.index),
    }
    return {
        "horizons": list(horizons),
        "groups": {k: summarize(v) for k, v in groups.items()},
        "note": "条件频率为历史统计，未做多重检验校正（8 组 × 4 周期 = 32 次比较），不得当作预测或交易依据。",
    }
