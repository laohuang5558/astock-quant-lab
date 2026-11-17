#!/usr/bin/env python3
"""内网既有「A股条件分析终端」(:8094) 只读体检取证器。

用途：把对 :8094 的实测结果固化为可复跑的机读证据，供改造评估与验收引用。

纪律：
- 全程只读 GET，不发 POST/DELETE，不触发任何任务、不写远端状态；
- 断言的是「观测事实」，不是「缺陷应当被修复」——含义解释交给评估报告，
  避免把体检器写成恒真的自我印证；
- 反向对照：--base 指向未监听端口时必须判 FAIL 且退出码 1；
- 正向 sanity：/api/meta 必须 200 且 n_stocks>0；不存在路径必须 404，
  证明 200 不是任意路径都能拿到。

产物：output/terminal_8094_probe.json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "terminal_8094_probe.json"
DEFAULT_BASE = "http://192.168.2.203:8094"
# 断言总数基线：预检短路时用于报告 FAIL(0/N)；run() 末尾会自检实际数量与之相等，
# 防止增删断言后忘记同步这个数字。
EXPECTED_TOTAL = 32


def get(base: str, path: str, timeout: float = 20.0):
    """返回 (status, parsed_or_text, bytes, headers)。永不抛异常。"""
    url = base.rstrip("/") + path
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        raw = r.read()
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            body = raw.decode("utf-8", "replace")
        return r.status, body, len(raw), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            body = raw.decode("utf-8", "replace")
        return e.code, body, len(raw), dict(e.headers)
    except Exception as e:  # noqa: BLE001 - 取证器需把网络异常转成判据
        return None, f"{type(e).__name__}: {e}", 0, {}


class Probe:
    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.evidence: dict = {}

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c["ok"])

    @property
    def total(self) -> int:
        return len(self.checks)


def first_item(body):
    if isinstance(body, dict):
        items = body.get("items")
        if isinstance(items, list) and items:
            return items[0]
    return None


def run(base: str) -> Probe:
    p = Probe()

    # ---- 0. 正向 sanity + 404 反证：证明 200 不是任意路径可得 ----
    s, meta, n, hdr = get(base, "/api/meta")
    p.evidence["meta_response"] = meta if isinstance(meta, dict) else str(meta)[:200]
    p.check("正向 sanity：/api/meta 返回 200 且 n_stocks > 0",
            s == 200 and isinstance(meta, dict) and int(meta.get("n_stocks", 0)) > 0,
            f"status={s} n_stocks={meta.get('n_stocks') if isinstance(meta, dict) else '?'}")
    s404, _, _, _ = get(base, "/this-path-does-not-exist-quantlab")
    p.check("404 反证：不存在路径返回 404", s404 == 404, f"status={s404}")

    # ---- 1. 契约面 ----
    s, spec, _, _ = get(base, "/openapi.json")
    paths = list(spec.get("paths", {})) if isinstance(spec, dict) else []
    p.evidence["openapi_paths"] = paths
    p.check("契约面：OpenAPI 3.1 且至少 8 个 API 路径",
            s == 200 and isinstance(spec, dict) and spec.get("openapi", "").startswith("3.") and len(paths) >= 8,
            f"status={s} paths={len(paths)}")
    pick_op = spec.get("paths", {}).get("/api/pick", {}).get("get", {}) if isinstance(spec, dict) else {}
    pick_params = {x.get("name") for x in pick_op.get("parameters", [])}
    p.evidence["pick_params"] = sorted(pick_params)
    p.check("契约面：/api/pick 声明了 preset 参数（用于后续取证）",
            "preset" in pick_params, f"params={sorted(pick_params)}")
    empty_schema = 0
    if isinstance(spec, dict):
        for _, ops in spec.get("paths", {}).items():
            for _, op in ops.items():
                if isinstance(op, dict):
                    r200 = op.get("responses", {}).get("200", {})
                    content = r200.get("content", {}) if isinstance(r200, dict) else {}
                    for _, ct in content.items():
                        sch = ct.get("schema", {}) if isinstance(ct, dict) else {}
                        if sch == {}:
                            empty_schema += 1
    p.evidence["empty_response_schemas"] = empty_schema
    p.check("取证：200 响应 schema 为空对象（不可用于生成客户端/校验）",
            empty_schema >= 5, f"count={empty_schema}")

    # ---- 2. 数据面板 ----
    s, stats, _, _ = get(base, "/api/stats")
    p.evidence["stats"] = stats
    p.check("数据面：/api/stats 返回分组计数且 total 与 meta 一致",
            s == 200 and isinstance(stats, dict) and stats.get("total") == (meta.get("n_stocks") if isinstance(meta, dict) else None),
            f"total={stats.get('total') if isinstance(stats, dict) else '?'}")

    # ---- 3. 取证 F1：preset 声明但不消费 ----
    presets = ["底部三绝", "强势选股 牛3>99 牛4>99", "临时选股 牛3>100 牛4>100"]
    results = []
    for name in presets:
        s, b, _, _ = get(base, "/api/pick?preset=" + quote(name) + "&limit=3")
        results.append(json.dumps(first_item(b), ensure_ascii=False, sort_keys=True) if s == 200 else f"status={s}")
    s_def, b_def, _, _ = get(base, "/api/pick?limit=3")
    default_first = json.dumps(first_item(b_def), ensure_ascii=False, sort_keys=True)
    p.evidence["preset_probe"] = {"presets": presets, "first_items": results, "default_first": default_first}
    p.check("取证 F1：不同 preset 与不带 preset 的返回首元素完全相同",
            len(set(results + [default_first])) == 1,
            f"distinct={len(set(results + [default_first]))}")

    # ---- 4. 取证 F2：无效 conds 静默回退 ----
    invalid = ["1/0", "__import__('os').getcwd()", "cci_w", "", "close>0 or 1==1"]
    inv_res = []
    for c in invalid:
        s, b, _, _ = get(base, "/api/pick?conds=" + quote(c) + "&limit=2")
        fi = json.dumps(first_item(b), ensure_ascii=False, sort_keys=True) if s == 200 else f"status={s}"
        inv_res.append({"conds": c, "status": s, "first": fi})
    s_ok, b_ok, _, _ = get(base, "/api/pick?conds=" + quote("cci_w>99") + "&limit=2")
    valid_first = json.dumps(first_item(b_ok), ensure_ascii=False, sort_keys=True)
    p.evidence["conds_probe"] = {"invalid": inv_res, "valid_first": valid_first}
    all_same_default = all(x["status"] == 200 and x["first"] == default_first for x in inv_res)
    p.check("取证 F2：无效/注入型 conds 全部 HTTP 200 且回退到默认选股结果",
            all_same_default, f"n={len(inv_res)} all_default={all_same_default}")
    p.check("取证 F2b：有效 conds 与默认结果不同（证明 conds 通道本身可用）",
            valid_first != default_first, "valid != default")

    # F2 精确机制：按分号逐条解析、静默丢弃无效项；全部无效才退化为默认结果。
    # 这一组用于把「静默错误」定位到「逐项吞掉」，而不是笼统的「整体回退」。
    s_mix, b_mix, _, _ = get(base, "/api/pick?conds=" + quote("cci_w>99;1/0") + "&limit=2")
    mix_first = json.dumps(first_item(b_mix), ensure_ascii=False, sort_keys=True)
    s_comma, b_comma, _, _ = get(base, "/api/pick?conds=" + quote("cci_w>99,cci_m>99") + "&limit=2")
    comma_first = json.dumps(first_item(b_comma), ensure_ascii=False, sort_keys=True)
    s_and, b_and, _, _ = get(base, "/api/pick?conds=" + quote("cci_w>99 AND cci_m>99") + "&limit=2")
    and_first = json.dumps(first_item(b_and), ensure_ascii=False, sort_keys=True)
    p.evidence["conds_silent_drop"] = {
        "valid_plus_invalid": mix_first == valid_first,
        "comma_separator": comma_first == default_first,
        "and_keyword": and_first == default_first,
    }
    p.check("取证 F2c：有效项+无效项混排时，无效项被静默丢弃（结果等同只有有效项）",
            s_mix == 200 and mix_first == valid_first, f"mix==valid: {mix_first == valid_first}")
    p.check("取证 F2d：逗号分隔 / AND 关键字不被支持，静默退化为默认结果",
            comma_first == default_first and and_first == default_first,
            f"comma_default={comma_first == default_first} and_default={and_first == default_first}")

    # ---- 5. 取证 F3：缺失值/ST 标的进入公式选股结果 ----
    s, pick, _, _ = get(base, "/api/pick?limit=300")
    items = pick.get("items", []) if isinstance(pick, dict) else []
    null_metric = [x for x in items if any(x.get(k) is None for k in ("adx", "pdi", "mdi"))]
    st_named = [x for x in items if "ST" in str(x.get("name", ""))]
    p.evidence["pick_quality"] = {
        "n": len(items),
        "null_metric_n": len(null_metric),
        "st_n": len(st_named),
        "null_examples": null_metric[:3],
        "st_examples": st_named[:3],
    }
    p.check("取证 F3：公式选股结果含指标为 null 的标的",
            len(null_metric) > 0, f"n={len(null_metric)} / {len(items)}")
    p.check("取证 F3b：公式选股结果含 ST 标的", len(st_named) > 0, f"n={len(st_named)}")

    s, g7, _, _ = get(base, "/api/scan?group=G7&limit=800")
    g7items = g7.get("items", []) if isinstance(g7, dict) else []
    g7_st = [x for x in g7items if "ST" in str(x.get("name", "")) or not x.get("name")]
    p.evidence["g7_quality"] = {"n": len(g7items), "st_or_unnamed": len(g7_st), "examples": g7_st[:3]}
    p.check("取证 F3c：G7（回踩+非死叉+窄幅）命中含 ST/无名标的",
            len(g7_st) > 0, f"n={len(g7_st)} / {len(g7items)}")

    # ---- 6. 取证 F4：胜率展示口径分裂（默认 limit=60 vs 全样本） ----
    s60, sg60, _, _ = get(base, "/api/signals/sz300398?group=G1")
    s600, sg600, _, _ = get(base, "/api/signals/sz300398?group=G1&limit=600")
    a60 = sg60.get("agg", {}) if isinstance(sg60, dict) else {}
    a600 = sg600.get("agg", {}) if isinstance(sg600, dict) else {}
    p.evidence["agg_probe"] = {
        "limit_default": {"items": len(sg60.get("items", [])) if isinstance(sg60, dict) else 0,
                          "total": sg60.get("total") if isinstance(sg60, dict) else None, "agg": a60},
        "limit_600": {"items": len(sg600.get("items", [])) if isinstance(sg600, dict) else 0,
                      "total": sg600.get("total") if isinstance(sg600, dict) else None, "agg": a600},
    }
    try:
        gap = abs(a60["fwd60"]["win"] - a600["fwd60"]["win"])
    except Exception:
        gap = 0.0
    p.evidence["agg_win_gap"] = round(gap, 4)
    p.check("取证 F4：默认 limit 的 fwd60 胜率与全样本相差 > 0.2",
            gap > 0.2, f"default={a60.get('fwd60', {}).get('win')} full={a600.get('fwd60', {}).get('win')} gap={gap:.4f}")
    p.check("取证 F4b：agg 的样本数 n 随前向周期递减（null 截断未披露）",
            (a60.get("fwd5", {}).get("n", 0) > a60.get("fwd20", {}).get("n", 0) > a60.get("fwd60", {}).get("n", 0)),
            f"n5/20/60={a60.get('fwd5', {}).get('n')}/{a60.get('fwd20', {}).get('n')}/{a60.get('fwd60', {}).get('n')}")
    keys = set((sg600 or {}).keys()) if isinstance(sg600, dict) else set()
    rigor_keys = keys & {"random_control", "oos", "pvalue", "benchmark", "cost_model", "multiple_testing"}
    p.evidence["rigor_keys_present"] = sorted(rigor_keys)
    p.check("取证 F4c：信号响应无随机对照/样本外/基准/成本/多重检验字段",
            len(rigor_keys) == 0, f"present={sorted(rigor_keys)} response_keys={sorted(keys)}")

    # ---- 7. 取证 F5：前向收益口径可反推复算，但契约未披露 ----
    # 公平性要点：初查用「收盘到收盘」口径自算不一致，一度被记为「无法复算」。
    # 反推后确认真实口径为 c[t+h]/o[t+1]-1（信号次日开盘买入）。此处用全样本
    # 三个周期做双口径对照：既证明算法自洽，也证明「收盘到收盘」不是它的口径。
    s_st, stock, _, _ = get(base, "/api/stock/sz300398?days=999999")
    daily = stock.get("daily", []) if isinstance(stock, dict) else []
    closes = {x["date"]: x["c"] for x in daily if isinstance(x, dict) and "date" in x and "c" in x}
    opens = {x["date"]: x["o"] for x in daily if isinstance(x, dict) and "date" in x and "o" in x}
    dates = sorted(closes)
    idx = {d: i for i, d in enumerate(dates)}
    items600 = sg600.get("items", []) if isinstance(sg600, dict) else []
    per_h = {}
    for h in (5, 20, 60):
        t1o, c2c = [], []
        for x in items600:
            d0, fwd = x.get("date"), x.get(f"fwd{h}")
            i = idx.get(d0)
            if i is None or i + h >= len(dates) or not isinstance(fwd, (int, float)):
                continue
            t1o.append(abs(closes[dates[i + h]] / opens[dates[i + 1]] - 1 - fwd))
            c2c.append(abs(closes[dates[i + h]] / closes[d0] - 1 - fwd))
        per_h[str(h)] = {
            "n": len(t1o),
            "t1_open_mean_abs_err": round(sum(t1o) / len(t1o), 8) if t1o else None,
            "t1_open_max_abs_err": round(max(t1o), 8) if t1o else None,
            "close_to_close_mean_abs_err": round(sum(c2c) / len(c2c), 6) if c2c else None,
        }
    all_t1_max = max((v["t1_open_max_abs_err"] or 9) for v in per_h.values())
    min_c2c = min((v["close_to_close_mean_abs_err"] or 0) for v in per_h.values())
    p.evidence["fwd_recompute"] = {
        "verified_formula": "fwd_h = close[t+h] / open[t+1] - 1",
        "per_horizon": per_h,
        "daily_len": len(dates),
        "daily_first": dates[0] if dates else None,
        "daily_last": dates[-1] if dates else None,
        "universe_start": meta.get("meta", {}).get("universe_start") if isinstance(meta, dict) else None,
    }
    p.check("取证 F5：三个周期均可用 c[t+h]/o[t+1]-1 复算，最大误差 < 1e-4（4 位小数舍入）",
            all(v["n"] and v["n"] >= 100 for v in per_h.values()) and all_t1_max < 1e-4,
            f"max_abs_err={all_t1_max:.8f} ns={[v['n'] for v in per_h.values()]}")
    p.check("取证 F5b：收盘到收盘口径误差显著更大，证明口径非它（避免误判为算错）",
            min_c2c > 0.005,
            f"min_close_to_close_mean_err={min_c2c:.6f}")
    p.check("取证 F5c：单股日线被截断在 2000 根左右，早于 meta.universe_start",
            len(dates) <= 2100 and (dates[0] if dates else "") > "2015-06-01",
            f"len={len(dates)} first={dates[0] if dates else '?'} universe_start=2015-06-01")
    # 口径是否披露：OpenAPI 里 /api/signals 的 200 schema 为空，无字段说明
    sig_op = spec.get("paths", {}).get("/api/signals/{code}", {}).get("get", {}) if isinstance(spec, dict) else {}
    sig_str = json.dumps(sig_op, ensure_ascii=False)
    disclosed = any(k in sig_str for k in ("fwd5", "open[t+1]", "次日开盘", "前向收益"))
    p.evidence["fwd_disclosed_in_contract"] = disclosed
    p.check("取证 F5d：OpenAPI 未披露前向收益口径（schema 为空、无字段说明）",
            not disclosed, f"disclosed={disclosed}")

    # ---- 8. 取证 F6：工程契约面 ----
    for path in ("/health", "/api/health"):
        s, _, _, _ = get(base, path)
        p.check(f"取证 F6：{path} 无健康检查端点（404）", s == 404, f"status={s}")
    s_neg, _, _, _ = get(base, "/api/stock/sz300398?days=-1")
    s_big, b_big, n_big, _ = get(base, "/api/stock/sz300398?days=999999")
    p.check("取证 F6b：days=-1 无边界校验仍返回 200", s_neg == 200, f"status={s_neg}")
    p.check("取证 F6c：days=999999 返回大响应体（无上限）", s_big == 200 and n_big > 100000, f"bytes={n_big}")
    s_bad, b_bad, _, _ = get(base, "/api/stock/NOTEXIST")
    p.check("取证 F6d：非法代码返回 FastAPI 原生 {detail} 而非统一信封",
            isinstance(b_bad, dict) and "detail" in b_bad and "ok" not in b_bad,
            f"status={s_bad} body={str(b_bad)[:80]}")
    hdr_keys = {k.lower() for k in hdr}
    p.evidence["missing_headers"] = sorted({"x-request-id", "cache-control", "content-security-policy",
                                            "x-content-type-options"} - hdr_keys)
    p.check("取证 F6e：/api/meta 响应无 request_id / 缓存 / 安全头",
            len(p.evidence["missing_headers"]) >= 3, f"missing={p.evidence['missing_headers']}")

    # ---- 9. 取证 F7：文档页外网 CDN 依赖 ----
    s_docs, docs, _, _ = get(base, "/docs")
    s_redoc, redoc, _, _ = get(base, "/redoc")
    cdn = [u for u, txt in (("cdn.jsdelivr.net", docs), ("fonts.googleapis.com", redoc))
           if isinstance(txt, str) and u in txt]
    p.evidence["docs_cdn"] = {"docs": "cdn.jsdelivr.net" in str(docs), "redoc_fonts": "fonts.googleapis.com" in str(redoc)}
    p.check("取证 F7：/docs 与 /redoc 依赖外网 CDN（离线/内网隔离会白屏）",
            len(cdn) >= 1, f"cdn={cdn}")
    s_ech, _, n_ech, _ = get(base, "/static/echarts.min.js")
    p.check("取证 F7b：主页面 ECharts 已本地托管（说明作者有零 CDN 意识）",
            s_ech == 200 and n_ech > 100000, f"status={s_ech} bytes={n_ech}")

    # ---- 10. 取证 F8：合规声明缺失 ----
    s_home, home, _, _ = get(base, "/")
    text = home if isinstance(home, str) else ""
    p.evidence["compliance"] = {
        "len": len(text),
        "has_not_advice": "不构成投资建议" in text,
        "has_disclaimer": "免责" in text,
        "has_risk": "风险" in text,
        "has_research_only": "research" in text.lower(),
        "has_only_caveat": "不代表可复制的胜率" in text,
        "selection_words": {w: text.count(w) for w in ("选股", "运行", "胜率", "操盘")},
    }
    p.check("取证 F8：首页含明确选股/胜率措辞",
            p.evidence["compliance"]["selection_words"].get("选股", 0) > 0 and
            p.evidence["compliance"]["selection_words"].get("胜率", 0) > 0,
            json.dumps(p.evidence["compliance"]["selection_words"], ensure_ascii=False))
    p.check("取证 F8b：首页无「不构成投资建议 / 免责 / research-only」声明",
            not p.evidence["compliance"]["has_not_advice"] and
            not p.evidence["compliance"]["has_disclaimer"] and
            not p.evidence["compliance"]["has_research_only"],
            f"has_only_caveat={p.evidence['compliance']['has_only_caveat']}")

    # 此处 p.total 尚未包含本条元断言本身，故期望 EXPECTED_TOTAL-1。
    p.check("元断言：实际断言数与基线一致（防止增删断言后总数失配）",
            p.total == EXPECTED_TOTAL - 1,
            f"before_meta={p.total} expected={EXPECTED_TOTAL - 1}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()

    # 快速连通性预检：服务不可达时立即短路，避免逐请求等待超时。
    # 这是反向对照的一部分——证明校验器不是恒真，且失败路径可被快速触发。
    pre_status, _, _, _ = get(args.base, "/api/meta", timeout=3.0)
    if pre_status is None:
        now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        payload = {
            "ok": False, "passed": 0, "total": EXPECTED_TOTAL,
            "method": "read-only GET", "base": args.base,
            "target": "A股条件分析终端 :8094 (内网既有服务，非本平台部署)",
            "probe_time": now,
            "checks": [{"name": "连通性预检：/api/meta 不可达", "ok": False,
                        "detail": "base 未监听或不可达，全部取证项判 FAIL"}],
            "evidence": {},
            "note": "反向对照：服务不可达时校验器必须判 FAIL 且退出码 1。",
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"TERMINAL_8094_PROBE: FAIL (0/{EXPECTED_TOTAL}) base={args.base} [连通性预检失败，短路]")
        print(f"EVIDENCE: {OUT}")
        return 1

    p = run(args.base)
    ok = p.passed == p.total
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    payload = {
        "ok": ok,
        "passed": p.passed,
        "total": p.total,
        "method": "read-only GET",
        "base": args.base,
        "target": "A股条件分析终端 :8094 (内网既有服务，非本平台部署)",
        "probe_time": now,
        "checks": p.checks,
        "evidence": p.evidence,
        "note": "断言对象为观测事实；缺陷含义、优先级与改造方案见 docs/TERMINAL_8094_REVIEW.md。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    label = "PASS" if ok else "FAIL"
    print(f"TERMINAL_8094_PROBE: {label} ({p.passed}/{p.total}) base={args.base}")
    for c in p.checks:
        if not c["ok"]:
            print(f"  MISS: {c['name']} | {c['detail']}")
    print(f"EVIDENCE: {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
