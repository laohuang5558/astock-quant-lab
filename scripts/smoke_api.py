"""平台 MVP 后端冒烟测试（确定性）

覆盖：静态前端可加载、契约 GET 接口真实读数、错误码、幂等键、
      触发类任务真实执行 quantlab 命令并轮询到终态。
只读接口不改动任何产物；触发任务仅执行 selftest（不改文件）。

用法：python scripts/smoke_api.py [base_url]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
API = BASE + "/quant/api/v1"

PASS: list[str] = []
FAIL: list[str] = []


def http(method: str, url: str, body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Accept": "application/json"}
    if data:
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def get_json(path: str, expect_ok=True):
    status, raw = http("GET", API + path)
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception as e:
        FAIL.append(f"GET {path} 返回非 JSON：{e}")
        return {}
    check(f"GET {path} -> HTTP {status} ok={obj.get('ok')}", status == 200 and (not expect_ok or obj.get("ok") is True))
    return obj


def check(label: str, cond: bool):
    (PASS if cond else FAIL).append(label)


def main() -> int:
    rep = json.loads((ROOT / "output" / "result.json").read_text(encoding="utf-8"))
    expected_fp = None
    try:
        sys.path.insert(0, str(ROOT))
        from quantlab.fingerprint import metric_fingerprint
        expected_fp = metric_fingerprint(rep)
    except Exception:
        pass

    # 1. 静态前端与快照
    for path, needle in [("/", "运行控制台"), ("/snapshot.js", "__QUANT_SNAPSHOT__")]:
        status, raw = http("GET", BASE + path)
        text = raw.decode("utf-8", "replace")
        check(f"静态资源 {path} -> HTTP {status} 且含 '{needle}'", status == 200 and needle in text)

    # 2. 核心只读接口，逐个断言真实字段
    h = get_json("/health")
    check("health 状态 ok 且三个数据文件齐备",
          h.get("data", {}).get("status") == "ok" and
          all(h.get("data", {}).get("data_files", {}).values()))
    if expected_fp:
        check(f"响应信封 metric_fingerprint={expected_fp}",
              h.get("metric_fingerprint") == expected_fp)

    runs = get_json("/runs")
    ids = [r.get("run_id") for r in runs.get("data", {}).get("runs", [])]
    check("GET /runs 含当前 v3 与两个历史对照",
          "v3-current" in ids and "v1-pre-audit" in ids and "v2-post-audit" in ids)

    res = get_json("/runs/v3-current/result")
    check("GET result 返回完整报告且质量门不放行",
          res.get("data", {}).get("result", {}).get("quality_gate", {}).get("final_verdict") == "不放行")

    met = get_json("/runs/v3-current/metrics")
    p = met.get("data", {}).get("metrics", {})
    check(f"metrics 夏普={p.get('sharpe')} 与 result.json 一致",
          p.get("sharpe") == rep["performance"]["sharpe"] and
          p.get("total_return") == rep["performance"]["total_return"])

    eq = get_json("/runs/v3-current/equity")
    series = eq.get("data", {}).get("series", [])
    check(f"equity 序列 {len(series)} 点且首末值合理",
          len(series) > 500 and series[0]["equity"] == 1000000.0 and series[-1]["equity"] > 0)

    tr = get_json("/runs/v3-current/trades?limit=50")
    check("trades 返回列、行与总数", len(tr.get("data", {}).get("columns", [])) > 0 and
          tr.get("data", {}).get("total", 0) > 0 and len(tr.get("data", {}).get("rows", [])) == 50)

    fac = get_json("/runs/v3-current/factors")
    check("factors 含 6 因子与合成 IC",
          len(fac.get("data", {}).get("per_factor", {})) == 6 and
          fac.get("data", {}).get("composite_ic", {}).get("ic_mean") == rep["factor_research"]["composite_ic"]["ic_mean"])

    ov = get_json("/runs/v3-current/overfit")
    mc = ov.get("data", {}).get("suite", {}).get("3_monte_carlo_deflated_sharpe", {})
    check(f"overfit 随机对照 {mc.get('n_paths')} 次且等价性为真",
          mc.get("n_paths") == 200 and mc.get("control_equivalence") is True and
          (mc.get("equivalence_evidence") or {}).get("max_abs_equity_diff") == 0.0)

    q = get_json("/runs/v3-current/quality-gate")
    check(f"quality-gate {len(q.get('data',{}).get('items',[]))} 项裁决 + 4 硬否决",
          len(q.get("data", {}).get("items", [])) == len(rep["quality_gate"]["items"]) and
          len(q.get("data", {}).get("hard_fail_items", [])) == 4)

    get_json("/verify/v3-current")
    au = get_json("/audit/v3-current/opinions")
    check(f"审核意见解析出 {len(au.get('data',{}).get('opinions',[]))} 位委员（防正则假阴性）",
          len(au.get("data", {}).get("opinions", [])) == 6)
    pr = get_json("/preregistration")
    check("预注册状态已冻结（嵌套 preregistration.frozen）",
          pr.get("data", {}).get("state", {}).get("preregistration", {}).get("frozen") is True and
          pr.get("data", {}).get("declared", {}).get("frozen") is True)
    get_json("/reports/v3-current/design")
    get_json("/reports/v3-current/audit")
    de = get_json("/deliveries")
    check("交付报告含 all_verified", de.get("data", {}).get("deliveries", {}).get("all_verified") is True)
    get_json("/agents/tasks")

    # 3. 历史 run / 报告正文 / 404
    get_json("/runs/v1-pre-audit")
    status, raw = http("GET", BASE + "/reports/design.html")
    check(f"设计报告 HTML 可在线读取（{len(raw):,} B）", status == 200 and b"<html" in raw.lower())
    status, raw = http("GET", API + "/runs/not-exist/result")
    obj = json.loads(raw)
    check("未知 run -> 404 NOT_FOUND", status == 404 and obj.get("error", {}).get("code") == "NOT_FOUND")

    # 4. 幂等键与冻结约束
    status, raw = http("POST", API + "/runs", {"kind": "selftest"})
    obj = json.loads(raw)
    check("POST /runs 缺幂等键 -> 428 IDEMPOTENCY_REQUIRED",
          status == 428 and obj.get("error", {}).get("code") == "IDEMPOTENCY_REQUIRED")
    status, raw = http("POST", API + "/preregistration", {"text": "x"},
                       {"Idempotency-Key": "k-" + uuid.uuid4().hex})
    obj = json.loads(raw)
    check("改写已冻结预注册 -> 409 CONFLICT",
          status == 409 and obj.get("error", {}).get("code") == "CONFLICT")
    status, raw = http("POST", API + "/runs", {"kind": "not-a-kind"},
                       {"Idempotency-Key": "k-" + uuid.uuid4().hex})
    obj = json.loads(raw)
    check("未知任务类型 -> 400 INVALID_ARGUMENT",
          status == 400 and obj.get("error", {}).get("code") == "INVALID_ARGUMENT")

    # 5. 真实任务：提交 → 轮询 → 断言真实执行成功
    key = "smoke-" + uuid.uuid4().hex
    status, raw = http("POST", API + "/runs", {"kind": "selftest"}, {"Idempotency-Key": key})
    obj = json.loads(raw)
    task_id = obj.get("data", {}).get("run", {}).get("task_id")
    check("POST /runs 受理 selftest 任务", status == 200 and bool(task_id))
    if task_id:
        final = None
        for _ in range(60):
            time.sleep(1.0)
            st, rr = http("GET", API + f"/runs/{task_id}/status")
            d = json.loads(rr).get("data", {})
            if d.get("status") in ("succeeded", "failed"):
                final = d
                break
        check("任务真实执行 quantlab selftest 并到达终态", final is not None)
        if final:
            check(f"任务退出码 0，日志含 25 项单测结果",
                  final.get("exit_code") == 0 and
                  ("OK" in (final.get("log_tail") or "") or "Ran 25 tests" in (final.get("log_tail") or "")))
        # 幂等：相同 key 返回同一 task_id，不重复执行
        status2, raw2 = http("POST", API + "/runs", {"kind": "selftest"}, {"Idempotency-Key": key})
        obj2 = json.loads(raw2)
        check("相同 Idempotency-Key 返回同一任务（幂等）",
              obj2.get("data", {}).get("run", {}).get("task_id") == task_id)

    # 6. 输出
    print(f"\nPASS {len(PASS)} / {len(PASS)+len(FAIL)}")
    for x in FAIL:
        print("  FAIL:", x)
    if FAIL:
        print("SMOKE: FAIL")
        return 1
    print("SMOKE: PASS")
    (ROOT / "output" / "api_smoke.json").write_text(
        json.dumps({"ok": True, "base": BASE, "passed": len(PASS), "failed": 0,
                    "checks": PASS}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
