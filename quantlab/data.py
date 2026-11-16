"""数据层：标准数据合约、CSV / 通达信 JSON 适配器、A 股交易约束面板。

数据合约（长表 CSV，一行 = 一个标的的一个交易日）：
    date, code, open, high, low, close, volume, amount [, name, industry, is_st, listed_date]

所有因子与回测只读取 <= T 日的数据；T+1 才允许成交，见 engine.py。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ["date", "code", "open", "high", "low", "close", "volume"]


def board_price_limit(code: str) -> float:
    """按板块返回涨跌停比例。ST 需另按 5% 处理（本框架直接剔除 ST）。"""
    c = str(code).zfill(6)
    if c.startswith(("30", "68")):
        return 0.20           # 创业板 / 科创板
    if c.startswith(("43", "83", "87", "92", "8", "4")):
        return 0.30           # 北交所
    return 0.10               # 沪深主板


def load_prices(path: str | Path) -> pd.DataFrame:
    """读取标准长表 CSV，补齐派生列并做基础质检。"""
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    df = pd.read_csv(p, dtype={"code": str})
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"数据合约缺列: {missing}")
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["code"] = df["code"].astype(str).str.zfill(6)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "amount" not in df.columns:
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        df["amount"] = df["volume"] * 100.0 * typical
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    if "is_st" not in df.columns:
        df["is_st"] = 0
    if "industry" not in df.columns:
        df["industry"] = "UNKNOWN"
    if "name" not in df.columns:
        df["name"] = df["code"]
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df["close"] > 0) & (df["volume"] >= 0)]
    df = df.drop_duplicates(subset=["date", "code"], keep="last")
    # 上市日：优先用显式 listed_date，否则以首个观测日作下界代理（口径已在文档登记）
    if "listed_date" in df.columns:
        df["listed_date"] = pd.to_datetime(df["listed_date"], errors="coerce")
    else:
        first = df.groupby("code")["date"].transform("min")
        df["listed_date"] = pd.to_datetime(first)
    return df.sort_values(["date", "code"]).reset_index(drop=True)


def from_tdx_kline_json(payload: dict | str | Path, code: str, name: str = "") -> pd.DataFrame:
    """把通达信 MCP tdx_kline 返回的 JSON 原样转成标准数据合约。

    tdx Rows 字段：Data / Open / High / Low / Close / Volume / Amount。
    Volume 单位已是「手」，Amount 单位已是「元」，按工具约定不再二次换算。
    """
    if isinstance(payload, (str, Path)):
        with open(payload, "r", encoding="utf-8") as f:
            payload = json.load(f)
    rows = payload.get("Rows") or []
    recs = []
    for r in rows:
        d = str(r.get("Data", ""))
        if len(d) != 8:
            continue
        recs.append({
            "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
            "code": str(code).zfill(6),
            "name": name or payload.get("AttachInfo", {}).get("Name", code),
            "open": float(r["Open"]), "high": float(r["High"]),
            "low": float(r["Low"]), "close": float(r["Close"]),
            "volume": float(r["Volume"]),
            "amount": float(r.get("Amount") or r.get("RawAmount") or 0.0),
            "is_st": 0, "industry": "UNKNOWN",
        })
    return pd.DataFrame(recs)


class MarketData:
    """把长表转成 日期 × 代码 的面板，并预算 A 股交易约束。"""

    def __init__(self, df: pd.DataFrame, cfg=None):
        self.df = df
        self.dates = sorted(df["date"].unique().tolist())
        self.codes = sorted(df["code"].unique().tolist())
        self.close = df.pivot(index="date", columns="code", values="close").sort_index()
        self.open = df.pivot(index="date", columns="code", values="open").sort_index()
        self.high = df.pivot(index="date", columns="code", values="high").sort_index()
        self.low = df.pivot(index="date", columns="code", values="low").sort_index()
        self.volume = df.pivot(index="date", columns="code", values="volume").sort_index()
        self.amount = df.pivot(index="date", columns="code", values="amount").sort_index()
        self.ret = self.close.pct_change(fill_method=None)
        self.prev_close = self.close.shift(1)

        # ---- 双价格口径（followup #9）----
        # 涨跌停基准必须用**未复权**前收：前复权序列会因未来除权而重算历史价，
        # 用它乘 1±limit 会在除权日产生虚假的涨跌停信号。收益/盯市仍用前复权价。
        self.raw_prices_loaded = False
        self.price_basis = "qfq_only"
        raw_path = cfg.get("data.raw_prices_csv") if cfg else None
        if raw_path:
            rp = Path(raw_path)
            if not rp.is_absolute():
                rp = Path(__file__).resolve().parent.parent / rp
            if rp.exists():
                rdf = pd.read_csv(rp, dtype={"code": str})
                rdf["code"] = rdf["code"].astype(str).str.zfill(6)
                rdf["date"] = pd.to_datetime(rdf["date"]).dt.strftime("%Y-%m-%d")
                cand_open = rdf.pivot(index="date", columns="code", values="open").sort_index().reindex(
                    index=self.dates, columns=self.codes)
                cand_close = rdf.pivot(index="date", columns="code", values="close").sort_index().reindex(
                    index=self.dates, columns=self.codes)
                coverage = float(cand_open.notna().sum().sum()) / max(1, len(self.dates) * len(self.codes))
                # 覆盖率不足（例如合成面板的日期不在真实样本区间内）时必须回退到
                # 前复权口径，否则 can_buy/can_sell 会全为 False、所有成交被静默拒绝。
                if coverage >= 0.5:
                    self.open_raw = cand_open
                    self.close_raw = cand_close
                    self.prev_close_raw = self.close_raw.shift(1)
                    self.raw_prices_loaded = True
                    self.price_basis = "raw_for_limits__qfq_for_pnl"
                    self.raw_coverage = round(coverage, 4)
                else:
                    self.price_basis = "qfq_only__raw_coverage_too_low"
                    self.raw_coverage = round(coverage, 4)
        base_prev = self.prev_close_raw if self.raw_prices_loaded else self.prev_close
        base_open = self.open_raw if self.raw_prices_loaded else self.open

        # ---- universe 配置项接线（followup #8）----
        exclude_st = bool(cfg.get("universe.exclude_st", True)) if cfg else True
        exclude_suspended = bool(cfg.get("universe.exclude_suspended", True)) if cfg else True
        min_days = int(cfg.get("universe.min_listed_days", 120)) if cfg else 120
        min_amt20 = float(cfg.get("universe.min_avg_amount_20d", 0) or 0) if cfg else 0.0

        lim = pd.Series({c: board_price_limit(c) for c in self.codes})
        self._lim = lim
        up = base_prev.mul(1.0 + lim, axis=1).round(2)
        dn = base_prev.mul(1.0 - lim, axis=1).round(2)
        self.limit_up = up
        self.limit_down = dn
        self.can_buy = base_open.notna() & (base_open < up - 1e-6)
        self.can_sell = base_open.notna() & (base_open > dn + 1e-6)
        self.tradable = self.close.notna()
        if exclude_suspended:
            self.tradable &= self.volume.fillna(0).gt(0)

        st = df.pivot(index="date", columns="code", values="is_st").sort_index().fillna(0)
        self.is_st = st.astype(bool)
        if "listed_date" in df.columns:
            listed = pd.to_datetime(df.groupby("code")["listed_date"].min())
        else:
            # 以首个观测日作为上市日下界代理（口径已在 docs/DATA_CONTRACT.md 登记）
            listed = pd.to_datetime(df.groupby("code")["date"].min())
        listed = listed.reindex(self.codes)
        age = pd.DataFrame(
            np.repeat(pd.to_datetime(pd.Series(self.dates)).values[:, None], len(self.codes), axis=1),
            index=self.dates, columns=self.codes,
        ) - pd.DataFrame(
            np.repeat(listed.reindex(self.codes).values[None, :], len(self.dates), axis=0),
            index=self.dates, columns=self.codes,
        )
        self.age_days = age.apply(lambda s: s.dt.days)
        amt20 = self.amount.rolling(20, min_periods=10).mean()
        if min_amt20 > 0:
            amt_ok = amt20.ge(min_amt20)
        else:
            amt_ok = pd.DataFrame(True, index=self.close.index, columns=self.close.columns)
        st_ok = ~self.is_st if exclude_st else pd.DataFrame(True, index=self.close.index,
                                                            columns=self.close.columns)
        self.eligible = (
            self.tradable
            & st_ok
            & amt_ok
            & (self.age_days >= min_days)
            & self.prev_close.notna()
        )
        self.industry = (
            df.pivot(index="date", columns="code", values="industry").sort_index().fillna("UNKNOWN")
        )
        self.wiring = {
            "exclude_st": exclude_st,
            "exclude_suspended": exclude_suspended,
            "min_listed_days": min_days,
            "min_avg_amount_20d": min_amt20,
            "min_avg_amount_20d_applied": bool(min_amt20 > 0),
            "raw_prices_loaded": self.raw_prices_loaded,
            "price_basis": self.price_basis,
            "raw_coverage": getattr(self, "raw_coverage", None),
        }
        self.amount_proxy_note = "amount 为成交额近似值（见 config.data.amount_proxy）"

    def slice(self, start: str | None = None, end: str | None = None) -> "MarketData":
        return MarketData(
            self.df[(self.df["date"] >= (start or "")) & (self.df["date"] <= (end or "9999"))],
            None,
        )

    def summary(self) -> dict:
        return {
            "dates": len(self.dates),
            "codes": len(self.codes),
            "start": self.dates[0] if self.dates else None,
            "end": self.dates[-1] if self.dates else None,
            "bars": int(self.close.notna().sum().sum()),
            "coverage": round(float(self.close.notna().sum().sum()) / max(1, len(self.dates) * len(self.codes)), 4),
        }
