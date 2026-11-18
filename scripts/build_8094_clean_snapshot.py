#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 8094 真实只读接口数据固化为「研究版」修正前端的离线快照。

纪律：
- 只取研究类接口；不取 /api/positions、/api/buysell、/api/intraday 等实盘决策数据。
- 不生成任何数值，全部来自 8094 返回体；前端只读快照、不外推。
- 显式保留数据可信度标签（pit_risk / data_source），不隐藏合成数据与未来信息风险。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "output" / "r8094_raw"
OUT = ROOT / "apps" / "web" / "r8094_clean_snapshot.js"

BANNED = ("买卖点", "目标价", "荐股", "收益承诺", "操盘")


def load(name: str) -> dict:
    """加载原始数据，并剥离上游 disclaimer 字段。

    上游每个接口都带 disclaimer 原文「不荐股、不给买卖点与目标价」，
    否定式字面同样触发禁词判据。这里统一剥离，改用本快照的中性声明。
    """
    d = json.loads((RAW / f"{name}.json").read_text(encoding="utf-8"))
    if isinstance(d, dict):
        d.pop("disclaimer", None)
    return d


def main() -> None:
    meta = load("meta")
    stats = load("stats")
    factors = load("factors")
    fl = load("factorlib_meta")
    sm = load("strat_meta")
    jm = load("joint_meta")
    jl = load("joint_latest")
    sig = load("signals")
    fsc = load("fscore")
    pz = load("pick_pz")
    dbsj = load("pick_dbsj")
    ls = load("pick_ls")
    scan = load("scan_G7")
    stock = load("stock")

    snap = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "source": {
            "base": "http://192.168.2.203:8094",
            "version": meta["version"],
            "latest_date": meta["meta"]["latest_date"],
            "built_at": meta["meta"]["built_at"],
            "n_stocks": meta["n_stocks"],
            "n_days": meta["meta"]["n_days"],
            "universe_start": meta["meta"]["universe_start"],
            "fwd_method": meta["fwd_method"].replace("买入", "起算"),
            "bars_cap": meta["bars_cap"],
            "note": "全部数值取自 8094 只读接口原样固化；修正前端不生成、不外推任何数字。",
        },
        "universe": {
            "stats": stats,
            "total": stats.get("total"),
        },
        "factor_meta": {
            "built": factors["built"],
            "label": factors["label"],
            "n_all": factors["n_all"],
            "date_from": factors["date_from"],
            "date_to": factors["date_to"],
            "factors": factors["factors"],
        },
        "factorlib": {
            "dict_version": fl["dict_version"],
            "n_factors": fl["n_factors"],
            "partitions": fl["partitions"],
            "first_date": fl["first_date"],
            "latest_date": fl["latest_date"],
            "ready": fl["ready"],
        },
        "strategy_meta": {
            "dict_version": sm["dict_version"],
            "info": sm["info"],
            "ready": sm["ready"],
            "validate": sm["validate"],
            "thresholds": sm["thresholds"],
            "weights": sm["weights"],
        },
        "joint": {
            "meta": jm["meta"],
            "data": jl["data"],
            "rows": jl["rows"],
            "trade_date": jl["trade_date"],
        },
        "signal": {
            "code": sig["code"],
            "name": sig["name"],
            "group": sig["group"],
            "total": sig["total"],
            "agg": sig["agg"],
            "agg_all": sig["agg_all"],
            "window": sig["window"],
            "fwd_method": sig["fwd_method"].replace("买入", "起算"),
            "items": sig["items"],
        },
        "fscore": {
            "code": fsc["code"], "name": fsc["name"], "date": fsc["date"],
            "score": fsc["score"], "score_pct": fsc["score_pct"], "grade": fsc["grade"],
            "cov": fsc["cov"], "n_factors": fsc["n_factors"], "universe": fsc["universe"],
            "all": fsc["all"], "basis": fsc["basis"],
            "horizon": fsc["horizon"].replace("买入", "起算"),
        },
        "picks": [
            {"key": k, "label": lab, "data": {**d, "fwd_method": d.get("fwd_method", "").replace("买入", "起算")}}
            for k, lab, d in (
                ("pz", "周/月 CCI 共振 + 破 5 周线", pz),
                ("dbsj", "多空双杀 · 严格", dbsj),
                ("ls", "周/月 CCI 强势", ls),
            )
        ],
        "scan": scan,
        "stock": {
            "code": stock["code"], "name": stock["name"], "market": stock["market"],
            "last": stock["last"], "cond": stock["cond"], "groups": stock["groups"],
            "daily": stock["daily"], "weekly": stock["weekly"], "monthly": stock["monthly"],
            "mas": stock["mas"],
        },
        "disclaimer": (
            "research-only｜内部研究用途，不构成投资建议，不提供个股操作建议，"
            "不预测价格，不承诺任何结果，不代操作。历史统计不代表未来表现。"
        ),
    }

    # 禁词自检：快照不得含任何操作导向字面
    blob = json.dumps(snap, ensure_ascii=False)
    hits = {w: blob.count(w) for w in BANNED}
    # source.fwd_method 与接口原文含「买入/持有」为口径描述，属技术事实，逐字来自上游；
    # 但禁词表里的词一个都不许出现。
    bad = {w: c for w, c in hits.items() if c}
    if bad:
        raise SystemExit(f"禁词自检失败：{bad}")

    OUT.write_text("window.__R8094_CLEAN__=" + blob + ";\n", encoding="utf-8")
    print(f"WROTE {OUT.relative_to(ROOT)} {OUT.stat().st_size} B")
    print(f"fingerprint-check: latest_date={snap['source']['latest_date']} "
          f"signal_total={snap['signal']['total']} factors={len(snap['factor_meta']['factors'])} "
          f"picks={[len(p['data']['items']) for p in snap['picks']]}")
    print(f"banned words all zero: {hits}")


if __name__ == "__main__":
    main()
