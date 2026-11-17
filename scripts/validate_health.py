#!/usr/bin/env python3
"""健康检查端点的确定性校验（D9：HTTP 状态码 + 返回 JSON 双证据）。

背景：曾出现把 ``/api/health`` 当作「已取得 200 与运行态」判据的表述。
实测该路径返回 404；契约里的 ``/health`` 必须拼接 servers 前缀，
完整 URL 为 ``http://127.0.0.1:8000/quant/api/v1/health``。

本校验从 contracts/openapi.yaml 解析 server base 与路径（不硬编码 URL），
再对运行中的服务做真实 HTTP 请求，逐项断言响应信封与运行态字段。
另设反向对照：请求错误路径 /api/health 与不存在的路径必须返回 404，
证明校验器不是恒真——若把任意路径都判为 200，反证项会失败。

用法：python scripts/validate_health.py [--base http://127.0.0.1:8000]
输出：output/health_check.json；未启动服务时判为失败（绝不静默通过）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENAPI = ROOT / "contracts" / "openapi.yaml"
SERVER_PY = ROOT / "apps" / "api" / "server.py"
OUT = ROOT / "output" / "health_check.json"

checks: list[dict] = []
issues: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "passed": bool(ok), "detail": detail})
    if not ok:
        issues.append(f"{name}: {detail}")


def get(url: str, timeout: float = 8.0) -> dict:
    """单次 GET，返回状态 / 原始字节 / 解析后的 JSON（若有）。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read()
            status, ctype = r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raw = e.read()
        status, ctype = e.code, e.headers.get("Content-Type", "")
    except (urllib.error.URLError, OSError) as e:
        return {"status": None, "error": f"{type(e).__name__}: {e}", "bytes": 0, "json": None}
    parsed = None
    if "json" in ctype or raw.lstrip().startswith(b"{"):
        try:
            parsed = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            parsed = None
    return {"status": status, "ctype": ctype, "bytes": len(raw), "json": parsed, "error": None}


def contract_base() -> tuple[str, str] | None:
    """从契约解析本地 server base 与 /health 路径，返回 (origin, prefix)。"""
    try:
        import yaml
    except ImportError:
        issues.append("缺少 PyYAML，无法解析 openapi.yaml；解析器缺失时不得判为通过")
        return None
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    servers = doc.get("servers") or []
    local = [s["url"] for s in servers if isinstance(s, dict)
             and "127.0.0.1" in str(s.get("url", ""))]
    check("契约含本地 server base", len(local) == 1, f"local_servers={local}")
    if len(local) != 1:
        return None
    base = local[0].rstrip("/")
    m = re.match(r"^(https?://[^/]+)(/.*)$", base)
    check("契约 server URL 可拆为 origin + 前缀", m is not None, f"server_url={base}")
    if not m:
        return None
    origin, prefix = m.group(1), m.group(2).rstrip("/")
    paths = doc.get("paths") or {}
    check("契约定义 GET /health", isinstance(paths.get("/health"), dict)
          and isinstance(paths["/health"].get("get"), dict), f"paths={sorted(paths)}")
    return origin, prefix


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None, help="覆盖契约里的 origin，仅用于端口变更时")
    args = ap.parse_args()

    src = OPENAPI.read_text(encoding="utf-8")
    check("openapi.yaml 可读取", len(src) > 0, f"{OPENAPI.relative_to(ROOT)}, {len(src)} B")

    parsed = contract_base()
    if parsed is None:
        result = {"ok": False, "checks": checks, "issues": issues,
                  "generated_at": datetime.now().isoformat(timespec="seconds")}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("HEALTH_CHECK: FAIL (契约解析失败)")
        for i in issues:
            print("  -", i)
        return 1

    origin, prefix = parsed
    if args.base:
        origin = args.base.rstrip("/")
    health_url = f"{origin}{prefix}/health"

    # 契约前缀与后端实现的一致性（静态，防契约/实现漂移）
    impl = SERVER_PY.read_text(encoding="utf-8")
    pm = re.search(r'API_PREFIX\s*=\s*"([^"]+)"', impl)
    impl_prefix = pm.group(1) if pm else None
    check("后端 API_PREFIX 与契约前缀一致", impl_prefix == prefix,
          f"impl={impl_prefix!r} contract={prefix!r}")

    # 正向：健康检查必须 200 + 完整信封
    pos = get(health_url)
    check("健康检查 HTTP 200", pos["status"] == 200,
          f"url={health_url} status={pos['status']} error={pos['error']}")
    body = pos["json"] or {}
    data = body.get("data") or {}
    check("响应含 application/json 且可解析", pos["json"] is not None,
          f"ctype={pos.get('ctype')} bytes={pos['bytes']}")
    check("响应信封 ok=true", body.get("ok") is True, f"ok={body.get('ok')!r}")
    check("运行态 data.status=ok", data.get("status") == "ok", f"status={data.get('status')!r}")
    check("服务标识为 astock-quant-platform", data.get("service") == "astock-quant-platform",
          f"service={data.get('service')!r}")
    check("引擎标注 research-only", "research-only" in str(data.get("engine", "")),
          f"engine={data.get('engine')!r}")
    files = data.get("data_files") or {}
    check("三项真实产物均可达", all(files.get(k) is True for k in
          ("result.json", "equity.csv", "trades.csv")), f"data_files={files}")
    check("信封携带 metric_fingerprint", bool(body.get("metric_fingerprint")),
          f"metric_fingerprint={body.get('metric_fingerprint')!r}")
    check("信封携带 request_id 与 server_time",
          bool(body.get("request_id")) and bool(body.get("server_time")),
          f"request_id={body.get('request_id')!r} server_time={body.get('server_time')!r}")

    # 反向对照 1：自检文本里的 /api/health 必须是 404
    wrong = get(f"{origin}/api/health")
    check("反向对照：/api/health 返回 404（该路径不是本服务健康端点）",
          wrong["status"] == 404, f"url={origin}/api/health status={wrong['status']}")
    # 反向对照 2：不存在路径也必须是 404，证明 200 不是任意路径都能拿到
    missing = get(f"{origin}{prefix}/definitely-not-an-endpoint")
    check("反向对照：不存在路径返回 404（校验器非恒真）",
          missing["status"] == 404, f"status={missing['status']}")

    result = {
        "ok": not issues,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "health_url": health_url,
        "contract_prefix": prefix,
        "positive_evidence": {
            "http_status": pos["status"],
            "response_json": body,
        },
        "negative_controls": {
            "/api/health": {"http_status": wrong["status"]},
            "nonexistent_path": {"http_status": missing["status"]},
        },
        "checks": checks,
        "passed": sum(1 for c in checks if c["passed"]),
        "total": len(checks),
        "issues": issues,
        "note": "健康端点必须拼接契约 servers 前缀；/api/health 返回 404，不得作为运行态判据。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"HEALTH_CHECK: {'PASS' if result['ok'] else 'FAIL'} "
          f"({result['passed']}/{result['total']})  {health_url}")
    for c in checks:
        print(f"  [{'ok' if c['passed'] else 'XX'}] {c['name']} — {c['detail']}")
    for i in issues:
        print("  ISSUE:", i)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
