#!/usr/bin/env python3
"""飞凯材料 KDJ 择时策略：规则形式化 + 事件驱动回测 + 等价随机对照。

用户原始描述（原话）：
    「飞凯材料的策略 上证指数现在每次新低有反弹。新低买，反弹t
      个股，看kdj，j值为负数，2负反弹」

本模块把这段口语规则拆成可证伪的形式化条件，而不是给出买卖建议：

  大盘条件 A：上证指数收盘创 N 日新低（默认 N=20）
  个股条件 B：KDJ(9,3,3) 的 J 值连续 M 日为负（「2负」=> M=2），
              且当日 J 较前一日回升（「反弹」/ 拐头确认）
  入场：A 与 B 同日（或可配 signal_window 日内错位）满足 -> T 收盘出信号，T+1 开盘买入
  出场（「反弹 T」）：J 上穿 0 / 止盈 / 止损 / 指数自低点反弹 / 持有上限
                     -> T 收盘判定，T+1 开盘卖出

「2负」存在歧义（连续两日 J<0 / J<-2 / 二次探底），因此不做单一猜测，
而是用 VARIANTS 参数网格把主要解释都跑一遍，并明确登记多重检验风险。

约束（与主工程一致）：T+1、涨跌停、佣金万 2.5 双边最低 ¥5、印花税千 1 卖出、
滑点 5bp、纯多头无杠杆（逐日 cash>=0 与敞口<=1 断言）、不含实盘下单。

口径折损（显式登记，不伪造）：成交价用前复权，涨跌停基准用未复权；
创业板涨跌幅按 2020-08-24 注册制前后 10%/20% 分段，属公开规则假设。
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

WARMUP = 60  # KDJ 递推初值与 N 日窗口需要预热，前 60 根不产生信号


@dataclass
class Params:
    name: str = "main"
    # 入场
    index_newlow_window: int = 20
    use_index_filter: bool = True
    signal_window: int = 0          # 0=A/B 必须同日；>0 允许大盘新低在最近 N 日内出现
    j_neg_consecutive: int = 2      # 「2负」
    j_threshold: float = 0.0        # J < 0
    require_j_turn: bool = True     # J 较前一日回升
    # 出场（按顺序判定）
    exit_j_cross_zero: bool = True
    exit_take_profit: float = 0.05
    exit_stop_loss: float = -0.05
    exit_max_hold: int = 10
    exit_index_rebound: float | None = None
    # 成本 / 规模
    capital: float = 1_000_000.0
    cost_mult: float = 1.0
    position_fraction: float = 1.0


def load_panel(as_of: str | None = None) -> pd.DataFrame:
    """把三套价格对齐到「完整交易日」，并计算 KDJ 与涨跌停基准。"""
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    if as_of is None:
        as_of = meta["last_complete_day"]

    q = pd.read_csv(DATA / "feikai_qfq.csv").set_index("date").sort_index()
    r = pd.read_csv(DATA / "feikai_raw.csv").set_index("date").sort_index()
    x = pd.read_csv(DATA / "index_sh000001.csv").set_index("date").sort_index()
    dates = q.index[q.index <= as_of]

    df = pd.DataFrame(index=dates)
    df["o"] = q["open"].reindex(dates).astype(float)
    df["h"] = q["high"].reindex(dates).astype(float)
    df["l"] = q["low"].reindex(dates).astype(float)
    df["c"] = q["close"].reindex(dates).astype(float)
    df["vol"] = q["volume"].reindex(dates).astype(float)
    df["ro"] = r["open"].reindex(dates).astype(float)       # 未复权开盘
    df["rpc"] = r["close"].reindex(dates).shift(1).astype(float)  # 未复权前收
    df["ic"] = x["close"].reindex(dates).astype(float)      # 上证指数收盘

    # 创业板：2020-08-24 注册制起 20%，之前 10%
    lim = np.where(np.array(dates) < "2020-08-24", 0.10, 0.20)
    df["up"] = (df["rpc"] * (1.0 + lim)).round(2)
    df["dn"] = (df["rpc"] * (1.0 - lim)).round(2)

    j, k, d = kdj(df["h"], df["l"], df["c"])
    df["j"], df["k"], df["d"] = j, k, d
    return df


def kdj(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 9, m1: int = 3, m2: int = 3, initial: float = 50.0):
    """通达信口径 KDJ：RSV -> SMA(RSV,3,1) -> SMA(K,3,1) -> J=3K-2D。

    初值 K=D=initial(50)；高低区间为 0 时 RSV 记 0（显式假设，已登记）。
    递推式 SMA(X,N,M): Y = (M*X + (N-M)*Y') / N。
    """
    ll = low.rolling(n, min_periods=n).min()
    hh = high.rolling(n, min_periods=n).max()
    rng = (hh - ll).to_numpy(float)
    c = close.to_numpy(float)
    l = ll.to_numpy(float)
    rsv = np.divide(c - l, rng, out=np.zeros(len(c), dtype=float), where=rng > 1e-12) * 100.0

    kval = np.empty(len(rsv), dtype=float)
    dval = np.empty(len(rsv), dtype=float)
    k_prev = d_prev = initial
    for i, x in enumerate(rsv):
        # SMA(X, m1, 1) / SMA(K, m2, 1)：Y = (X + (N-1)*Y') / N
        k_prev = (x + (m1 - 1) * k_prev) / m1
        d_prev = (k_prev + (m2 - 1) * d_prev) / m2
        kval[i], dval[i] = k_prev, d_prev
    return 3.0 * kval - 2.0 * dval, kval, dval


def build_signals(df: pd.DataFrame, p: Params) -> pd.Series:
    """生成 T 日收盘信号（T+1 开盘执行）。"""
    j = df["j"]
    neg = j < p.j_threshold
    consec = neg.rolling(p.j_neg_consecutive, min_periods=p.j_neg_consecutive).sum() >= p.j_neg_consecutive
    turn = j > j.shift(1)
    b = consec & (turn if p.require_j_turn else True)

    ic = df["ic"]
    w = p.index_newlow_window
    roll_min = ic.rolling(w, min_periods=w).min()
    a = ic <= roll_min + 1e-9
    if p.signal_window > 0:
        a = a.rolling(p.signal_window + 1, min_periods=1).max().astype(bool)
    sig = b & (a if p.use_index_filter else True)

    sig = sig & df["c"].notna() & df["ic"].notna()
    sig.iloc[:WARMUP] = False
    sig.iloc[-1] = False  # 最后一根无法 T+1 执行
    return sig.fillna(False).astype(bool)


def backtest(df: pd.DataFrame, p: Params, signals: pd.Series | None = None) -> dict:
    """单标的事件驱动回测；signals 可覆盖，用于等价随机对照。"""
    if signals is None:
        signals = build_signals(df, p)
    dates = list(df.index)
    o = df["o"].to_numpy(float)
    c = df["c"].to_numpy(float)
    ro = df["ro"].to_numpy(float)
    up = df["up"].to_numpy(float)
    dn = df["dn"].to_numpy(float)
    j = df["j"].to_numpy(float)
    ic = df["ic"].to_numpy(float)
    sig = signals.reindex(dates).fillna(False).to_numpy(bool)

    commission_rate = 0.00025 * p.cost_mult
    stamp_rate = 0.001 * p.cost_mult
    min_commission = 5.0 * p.cost_mult
    slip = 0.0005 * p.cost_mult

    cash = float(p.capital)
    shares = 0.0
    pending_buy = False
    pending_sell = False
    entry: dict | None = None
    hold = 0
    cost_total = 0.0
    eq: list[float] = []
    cash_hist: list[float] = []
    exp_hist: list[float] = []
    trades: list[dict] = []
    blocked: list[dict] = []
    cash_min = cash
    max_exposure = 0.0

    for i, day in enumerate(dates):
        # ---- 1. 执行昨日 pending（T+1 开盘）----
        if pending_buy:
            tradable = np.isfinite(ro[i]) and ro[i] > 0 and np.isfinite(up[i]) and ro[i] < up[i] - 1e-6
            if tradable and np.isfinite(o[i]) and o[i] > 0:
                price = o[i] * (1.0 + slip)
                budget = cash * p.position_fraction
                sh_i = budget / (price * (1.0 + commission_rate))
                gross = sh_i * price
                comm = max(gross * commission_rate, min_commission)
                if gross + comm > budget + 1e-9:
                    sh_i = max((budget - min_commission) / price, 0.0)
                    gross = sh_i * price
                    comm = max(gross * commission_rate, min_commission)
                if sh_i > 0 and cash - gross - comm >= -1e-6:
                    cash -= gross + comm
                    cost_total += comm + sh_i * o[i] * slip
                    shares = sh_i
                    entry = {"date": day, "i": i, "price": price,
                             "nominal_price": float(o[i]), "shares": sh_i,
                             "j": float(j[i]) if np.isfinite(j[i]) else None,
                             "index_close": float(ic[i]) if np.isfinite(ic[i]) else None,
                             "cost": comm + sh_i * o[i] * slip}
                    hold = 0
            else:
                blocked.append({"date": day, "side": "BUY", "reason": "涨停/停牌/无有效开盘价"})
            pending_buy = False

        elif pending_sell:
            tradable = np.isfinite(ro[i]) and np.isfinite(dn[i]) and ro[i] > dn[i] + 1e-6
            if tradable and np.isfinite(o[i]) and o[i] > 0 and shares > 0:
                price = o[i] * (1.0 - slip)
                gross = shares * price
                comm = max(gross * commission_rate, min_commission)
                stamp = gross * stamp_rate
                cash += gross - comm - stamp
                cost_total += comm + stamp + shares * o[i] * slip
                assert entry is not None
                # 毛价差用「无滑点名义开盘价」计算，滑点统一记进成本，避免重复扣减：
                # 现金变化 = sh*(o_sell-o_buy) - 滑点 - 佣金 - 印花税 = gross_pnl - trade_cost
                gross_pnl = (o[i] - entry["nominal_price"]) * shares
                trade_cost = entry["cost"] + comm + stamp + shares * o[i] * slip
                trades.append({
                    "entry_date": entry["date"], "exit_date": day,
                    "entry_price": round(entry["price"], 4), "exit_price": round(price, 4),
                    "shares": round(shares, 2), "hold_days": hold,
                    "gross_pnl_cny": round(gross_pnl, 2),
                    "cost_cny": round(trade_cost, 2),
                    "net_pnl_cny": round(gross_pnl - trade_cost, 2),
                    "return_pct": round((price / entry["price"] - 1.0) * 100.0, 4),
                    "exit_reason": entry.get("exit_reason", "unknown"),
                    "entry_j": (round(entry["j"], 4) if entry["j"] is not None else None),
                })
                shares = 0.0
                entry = None
                hold = 0
                pending_sell = False
            else:
                blocked.append({"date": day, "side": "SELL", "reason": "跌停/停牌，顺延下一交易日重试"})
                # 保持 pending_sell=True，不放弃未成交卖出

        # ---- 2. 收盘盯市 ----
        mark = c[i] if np.isfinite(c[i]) else (c[i - 1] if i > 0 and np.isfinite(c[i - 1]) else 0.0)
        equity = cash + shares * mark
        eq.append(equity)
        cash_hist.append(cash)
        exposure = (shares * mark / equity) if equity > 0 else 0.0
        exp_hist.append(exposure)
        cash_min = min(cash_min, cash)
        max_exposure = max(max_exposure, exposure)
        if cash < -1e-6:
            raise AssertionError(f"违反纯多头无杠杆：{day} cash={cash:.6f}")
        if exposure > 1.0 + 1e-6:
            raise AssertionError(f"违反纯多头无杠杆：{day} exposure={exposure:.6f}")

        # ---- 3. 收盘生成次日 pending ----
        if i >= len(dates) - 1:
            continue
        if shares == 0.0 and entry is None and sig[i]:
            pending_buy = True
        elif shares > 0.0 and entry is not None:
            hold += 1
            reason = None
            if p.exit_j_cross_zero and i > 0 and np.isfinite(j[i]) and np.isfinite(j[i - 1]) \
                    and j[i] > 0 >= j[i - 1]:
                reason = "j_cross_zero"
            if reason is None and np.isfinite(c[i]):
                r = c[i] / entry["price"] - 1.0
                if r >= p.exit_take_profit:
                    reason = "take_profit"
                elif r <= p.exit_stop_loss:
                    reason = "stop_loss"
            if reason is None and p.exit_index_rebound is not None:
                seg = ic[entry["i"]:i + 1]
                seg = seg[np.isfinite(seg)]
                if len(seg) and ic[i] / float(seg.min()) - 1.0 >= p.exit_index_rebound:
                    reason = "index_rebound"
            if reason is None and hold >= p.exit_max_hold:
                reason = "max_hold"
            if reason:
                entry["exit_reason"] = reason
                pending_sell = True

    # 末尾强制以最后收盘价平仓（估值口径，避免把未实现盈亏藏起来）
    open_position = None
    if shares > 0 and entry is not None:
        open_position = {"entry_date": entry["date"], "shares": round(shares, 2),
                         "mark_price": round(float(c[-1]), 4), "hold_days": hold,
                         "exit_reason": "forced_mark_at_sample_end"}

    eqs = pd.Series(eq, index=pd.Index(dates, name="date"))
    perf = performance(eqs, p.capital, cost_total, trades, cash_min, max_exposure)
    return {
        "params": asdict(p),
        "equity": eqs,
        "trades": trades,
        "blocked": blocked,
        "open_position": open_position,
        "cost_total_cny": round(cost_total, 2),
        "cash_min_cny": round(cash_min, 2),
        "max_position_exposure": round(max_exposure, 6),
        "perf": perf,
        "n_signals": int(sig.sum()),
        "exposure_days": int(sum(1 for e in exp_hist if e > 1e-9)),
        "n_days": len(dates),
    }


def performance(eqs: pd.Series, capital: float, cost_total: float,
                trades: list[dict], cash_min: float, max_exposure: float) -> dict:
    r = eqs.pct_change().dropna()
    total = float(eqs.iloc[-1] / capital - 1.0)
    years = max(len(eqs) / 252.0, 1e-9)
    ann = (1.0 + total) ** (1.0 / years) - 1.0 if total > -1 else -1.0
    vol = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    sharpe = float(r.mean() / vol * math.sqrt(252)) if vol > 0 else 0.0
    dd = eqs / eqs.cummax() - 1.0
    mdd = float(dd.min())
    wins = [t for t in trades if t["net_pnl_cny"] > 0]
    return {
        "total_return": round(total, 6),
        "annualized_return": round(ann, 6),
        "sharpe": round(sharpe, 6),
        "volatility_annual": round(vol * math.sqrt(252), 6),
        "max_drawdown": round(mdd, 6),
        "calmar": round(ann / abs(mdd), 6) if mdd < 0 else None,
        "n_trades": len(trades),
        "n_closed_trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 6) if trades else None,
        "avg_hold_days": round(float(np.mean([t["hold_days"] for t in trades])), 2) if trades else None,
        "avg_trade_return_pct": round(float(np.mean([t["return_pct"] for t in trades])), 4) if trades else None,
        "cost_total_cny": float(round(float(cost_total), 2)),
        "cash_min_cny": float(round(float(cash_min), 2)),
        "max_position_exposure": float(round(float(max_exposure), 6)),
        "final_equity_cny": float(round(float(eqs.iloc[-1]), 2)),
    }


def slice_perf(res: dict, start: str, end: str, capital: float) -> dict:
    """样本内 / 样本外切片绩效（跨切点持仓按净值截断，口径已登记）。"""
    e = res["equity"]
    seg = e[(e.index >= start) & (e.index <= end)]
    if len(seg) < 2:
        return {"n_days": len(seg), "note": "区间样本不足"}
    base = float(seg.iloc[0])
    r = seg.pct_change().dropna()
    total = float(seg.iloc[-1] / base - 1.0)
    vol = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    sharpe = float(r.mean() / vol * math.sqrt(252)) if vol > 0 else 0.0
    dd = seg / seg.cummax() - 1.0
    trades = [t for t in res.get("trades", []) if start <= t["entry_date"] <= end]
    return {
        "n_days": len(seg),
        "total_return": round(total, 6),
        "sharpe": round(sharpe, 6),
        "max_drawdown": round(float(dd.min()), 6),
        "n_trades": len(trades),
        "win_rate": round(sum(1 for t in trades if t["net_pnl_cny"] > 0) / len(trades), 6) if trades else None,
        "note": "以区间首日净值为分母；跨越切点的持仓按净值截断，不计为区间内平仓",
    }


def buy_and_hold(df: pd.DataFrame, p: Params) -> dict:
    """同期买入持有基准（同样含佣金/印花税/滑点与 T+1）。"""
    dates = list(df.index)
    o = df["o"].to_numpy(float)
    c = df["c"].to_numpy(float)
    rate, minc, slip = 0.00025 * p.cost_mult, 5.0 * p.cost_mult, 0.0005 * p.cost_mult
    price = o[0] * (1 + slip)
    sh = p.capital / (price * (1 + rate))
    gross = sh * price
    comm = max(gross * rate, minc)
    sh = (p.capital - comm) / price
    cash = p.capital - (sh * price + max(sh * price * rate, minc))
    # 与策略主回测一致：全程按收盘价盯市，末日不做卖出撮合，
    # 避免把开盘平仓造成的假跳变混进日收益序列（影响夏普/波动）。
    eqs = pd.Series(cash + sh * c, index=pd.Index(dates))
    sell = o[-1] * (1 - slip) * sh
    stamp = sell * 0.001 * p.cost_mult
    sell_comm = max(sell * rate, minc)
    liquidation = cash + sell - stamp - sell_comm
    total_cost = comm + sh * o[0] * slip + stamp + sell_comm + sh * o[-1] * slip
    return {
        "equity": eqs,
        "liquidation_value_cny": round(float(liquidation), 2),
        "perf": performance(eqs, p.capital, total_cost, [], 0.0, 1.0),
        "note": "买入持有基准按收盘盯市；变现价值单列，未平仓故 n_trades=0。",
    }


def index_newlow_study(df: pd.DataFrame, window: int = 20,
                       horizons: tuple[int, ...] = (1, 3, 5, 10),
                       scopes: tuple[tuple[str, str, str], ...] | None = None) -> dict:
    """检验用户前提：「上证指数每次新低有反弹」——客观统计，不构成预测。"""
    if scopes is None:
        scopes = (("全样本 2018-2026", "2018-01-01", "2026-12-31"),
                  ("近一年", "2025-11-17", "2026-12-31"))
    ic = df["ic"]
    roll_min = ic.rolling(window, min_periods=window).min()
    nl = (ic <= roll_min + 1e-9) & ic.notna() & roll_min.notna()
    out = {}
    for label, s, e in scopes:
        mask = (ic.index >= s) & (ic.index <= e)
        rows = {}
        for h in horizons:
            fwd = ic.shift(-h) / ic - 1.0
            v = fwd[nl & mask].dropna()
            if len(v) == 0:
                rows[str(h)] = {"n": 0}
                continue
            rows[str(h)] = {
                "n": int(len(v)),
                "up_ratio": round(float((v > 0).mean()), 6),
                "mean": round(float(v.mean()), 6),
                "median": round(float(v.median()), 6),
                "p25": round(float(v.quantile(0.25)), 6),
                "p75": round(float(v.quantile(0.75)), 6),
                "min": round(float(v.min()), 6),
                "max": round(float(v.max()), 6),
            }
        rows["n_newlow_days"] = int((nl & mask).sum())
        out[label] = rows
    return {"window": window, "horizons": list(horizons), "scopes": out,
            "note": "统计的是历史条件频率，非未来预测；末 h 日无未来收益的样本已剔除。"}


def equivalent_random_control(df: pd.DataFrame, p: Params, base: dict,
                              n_paths: int = 200, seed0: int = 9000) -> dict:
    """等价随机对照：只替换信号日，其余（引擎/成本/退出/资金）完全一致。

    随机信号数量与真实信号数量一致，抽样池为同样的合法日期，重叠信号由引擎
    自行忽略（与真实信号路径相同），因此差异只能来自「选时是否有信息」。
    """
    dates = list(df.index)
    real = build_signals(df, p)
    eligible = np.array([i for i, d in enumerate(dates)
                         if WARMUP <= i < len(dates) - 1])
    n_sig = int(real.sum())
    if n_sig == 0:
        return {"status": "no_signals", "note": "真实规则未产生任何信号，无法做显著性对照"}
    if n_sig > len(eligible):
        return {"status": "not_evaluable", "n_signals": n_sig, "eligible_days": len(eligible)}
    rng = np.random.default_rng(seed0)
    sharpes, totals = [], []
    for i in range(n_paths):
        # 无放回抽样保证每条路径的信号数量与真实规则严格一致
        pick = rng.choice(eligible, size=n_sig, replace=False)
        s = pd.Series(False, index=pd.Index(dates))
        s.iloc[pick] = True
        s.iloc[-1] = False
        r = backtest(df, p, signals=s)
        sharpes.append(r["perf"]["sharpe"])
        totals.append(r["perf"]["total_return"])
    sharpes = np.array(sharpes, dtype=float)
    totals = np.array(totals, dtype=float)
    b_sh = base["perf"]["sharpe"]
    b_tr = base["perf"]["total_return"]
    return {
        "status": "evaluated",
        "n_paths": n_paths,
        "n_signals_per_path": n_sig,
        "random_sharpe_mean": round(float(sharpes.mean()), 6),
        "random_sharpe_p95": round(float(np.quantile(sharpes, 0.95)), 6),
        "random_total_return_mean": round(float(totals.mean()), 6),
        "baseline_sharpe": round(float(b_sh), 6),
        "baseline_total_return": round(float(b_tr), 6),
        "sharpe_percentile": round(float((sharpes < b_sh).mean()), 4),
        "return_percentile": round(float((totals < b_tr).mean()), 4),
        "significantly_better_than_random": bool((sharpes < b_sh).mean() >= 0.95 and (totals < b_tr).mean() >= 0.95),
        "note": "等价性来自同一 backtest() 与同一退出/成本规则；仅信号日随机化。",
    }
