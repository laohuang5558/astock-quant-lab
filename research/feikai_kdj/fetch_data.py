#!/usr/bin/env python3
"""抓取「飞凯材料 / 上证指数」真实日线，供单标的 KDJ 择时策略研究使用。

数据源：腾讯证券公开行情接口 web.ifzq.gtimg.cn/appstock/app/fqkline/get
数据等级：L3 第三方公开行情，非交易所一手数据。

三套价格严格分开，禁止混用（口径沿用 docs/DATA_CONTRACT.md）：
  1) feikai_qfq.csv      飞凯材料前复权  -> 收益、盯市、成交价、KDJ 计算
  2) feikai_raw.csv      飞凯材料未复权  -> 涨跌停基准（创业板 20%）
  3) index_sh000001.csv  上证指数        -> 「创 N 日新低」大盘条件

重要：抓取当日若处于交易时段，未复权 / 指数通道会返回一根未收盘的盘中 bar。
该 bar 仅允许用于「当前状态」展示，禁止进入回测样本。完整交易日以上述
qfq 通道最后一根为准，回测统一切到 meta.json 的 last_complete_day。

仅用于研究与回测，不含任何实盘下单能力。
"""
from __future__ import annotations

import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://gu.qq.com/",
}
FIELDS = ["date", "code", "open", "high", "low", "close", "volume", "amount"]

START = "2018-01-01"
END = "2026-11-17"


def fetch_series(symbol: str, code: str, start: str, end: str,
                 adjust: str = "qfq", retries: int = 3) -> list[dict]:
    """按 2 年一段分页，规避单次返回条数上限。"""
    out: dict[str, dict] = {}
    y0, y1 = int(start[:4]), int(end[:4])
    for y in range(y0, y1 + 1, 2):
        seg_start = max(start, f"{y}-01-01")
        seg_end = min(end, f"{y + 1}-12-31")
        suffix = "qfq" if adjust == "qfq" else ""
        param = f"{symbol},day,{seg_start},{seg_end},800,{suffix}"
        url = ENDPOINT + "?" + urllib.parse.urlencode({"param": param})
        payload = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=20) as resp:
                    payload = json.load(resp)
                break
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(1.0 + attempt)
        node = (payload.get("data") or {}).get(symbol) or {}
        bars = (node.get("qfqday") or []) if adjust == "qfq" else (node.get("day") or [])
        for b in bars:
            if len(b) < 6:
                continue
            try:
                d, o, c, h, l, v = b[0], float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])
            except (TypeError, ValueError):
                continue
            if min(o, c, h, l) <= 0 or h < max(o, c) or l > min(o, c):
                continue  # 丢弃明显损坏的 bar
            typical = (h + l + c) / 3.0
            out[str(d)] = {
                "date": str(d), "code": code, "open": o, "high": h, "low": l,
                "close": c, "volume": v, "amount": round(v * 100.0 * typical, 2),
            }
        time.sleep(0.12)
    return [out[k] for k in sorted(out)]


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    specs = [
        ("feikai_qfq.csv", "sz300398", "300398", "qfq"),
        ("feikai_raw.csv", "sz300398", "300398", "none"),
        ("index_sh000001.csv", "sh000001", "000001", "none"),
    ]
    series: dict[str, list[dict]] = {}
    for fname, symbol, code, adjust in specs:
        rows = fetch_series(symbol, code, START, END, adjust=adjust)
        if len(rows) < 500:
            raise SystemExit(f"{fname} 仅取到 {len(rows)} 根，疑似接口截断，已中止（禁止用残缺数据冒充完整样本）")
        write_csv(DATA / fname, rows)
        series[fname] = rows
        print(f"WROTE {fname:24} rows={len(rows):>5}  {rows[0]['date']} ~ {rows[-1]['date']}", flush=True)

    qfq_days = {r["date"] for r in series["feikai_qfq.csv"]}
    raw_days = {r["date"] for r in series["feikai_raw.csv"]}
    idx_days = {r["date"] for r in series["index_sh000001.csv"]}
    intraday = sorted((raw_days | idx_days) - qfq_days)
    last_complete = max(qfq_days)

    # 名称跨源校验：qt 字段里的中文名必须与用户指定标的、指数一致
    names = {}
    for symbol in ("sz300398", "sh000001"):
        param = f"{symbol},day,2026-11-01,2026-11-17,20,"
        url = ENDPOINT + "?" + urllib.parse.urlencode({"param": param})
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as r:
            d = json.load(r)
        qt = ((d.get("data") or {}).get(symbol) or {}).get("qt", {}).get(symbol) or []
        names[symbol] = qt[1] if len(qt) > 1 else "UNKNOWN"
    if names.get("sz300398") != "飞凯材料" or names.get("sh000001") != "上证指数":
        raise SystemExit(f"标的名称校验失败：{names}")
    print("NAME CHECK:", names)

    meta = {
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "腾讯证券 web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        "level": "L3 第三方公开行情，非交易所一手数据",
        "symbols": {"300398": "飞凯材料（创业板，涨跌幅 20%）", "000001": "上证指数"},
        "name_check": names,
        "requested_range": [START, END],
        "rows": {k: len(v) for k, v in series.items()},
        "last_complete_day": last_complete,
        "intraday_bars": intraday,
        "intraday_note": (
            "未复权 / 指数通道在交易时段会返回当日未收盘 bar；该 bar 仅用于展示当前状态，"
            "回测样本一律截断到 last_complete_day，避免把盘中快照当成已实现收盘价。"
        ),
        "price_basis": {
            "pnl_and_signal": "feikai_qfq.csv 前复权",
            "limit_up_down": "feikai_raw.csv 未复权前收 + 未复权开盘",
            "timing": "index_sh000001.csv 上证指数收盘",
        },
        "caveats": [
            "amount 为 volume×100×典型价近似，非交易所真实成交额，不得用于容量测算。",
            "前复权价会随后续除权除息变化，历史回测中的绝对价格口径与实时行情不同。",
            "成交价仍用前复权价，涨跌停基准用未复权价，属已登记的半步口径折损；彻底分离需除权除息流水。",
            "未建模分红再投资、停牌复牌现金处理、冲击成本与整手取整。",
        ],
        "compliance": "research-only，不含实盘下单、券商接口、自动交易循环；不荐股、不给买卖点与目标价、不承诺收益、不代操作。",
    }
    (DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE meta.json  last_complete_day=", last_complete, " intraday=", intraday)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
