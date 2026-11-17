#!/usr/bin/env python3
"""内网既有 Agent 工作台（:8093）只读探测确定性校验。

背景：用户给出 http://192.168.2.203:8093/。该服务是内网既有的
「Agent 工作台 · ai-server」，不是本平台部署的页面。它的角色带
run_shell / sql_query 工具，且历史任务事件里留有真实执行记录；
这与交付文档中「内网无 Python 执行通道与数据库账号」的旧结论冲突，
故把「该通道存在」这一事实固化为可复跑校验。

安全边界（硬性）：本脚本只发 GET，绝不调用 POST /api/tasks、
DELETE /api/artifacts 或任何会触发 LLM 任务 / 服务器写操作的端点。
数据库账号与 pgvector 的结论来自工作台既有产物文本，不是本脚本直连 PG
取得的凭据；脚本不连接 5432，也不假设任何账号密码。

用法：python scripts/validate_workbench.py [--base http://192.168.2.203:8093]
输出：output/workbench_probe.json；服务不可达时判为失败（不静默通过）。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "workbench_probe.json"
DEFAULT_BASE = "http://192.168.2.203:8093"

checks: list[dict] = []
issues: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "passed": bool(ok), "detail": detail})
    if not ok:
        issues.append(f"{name}: {detail}")


def get(base: str, path: str, timeout: float = 8.0) -> dict:
    """只发 GET。返回状态码与解析后的 JSON；任何非 2xx 都不算证据。"""
    url = base.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        return {"url": url, "status": e.code, "json": None,
                "error": e.read()[:200].decode("utf-8", "replace")}
    except (urllib.error.URLError, OSError) as e:
        return {"url": url, "status": None, "json": None,
                "error": f"{type(e).__name__}: {e}"}
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        parsed = None
    return {"url": url, "status": status, "json": parsed, "error": None,
            "bytes": len(raw)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    health = get(base, "/api/health")
    check("工作台健康检查 HTTP 200", health["status"] == 200,
          f"url={health['url']} status={health['status']} error={health['error']}")
    hj = health["json"] or {}
    check("健康检查 ok=true 且标注 LLM 可用", hj.get("ok") is True and hj.get("llm") is True,
          f"health={hj}")

    env = get(base, "/api/env")
    ej = env["json"] or {}
    check("环境读数返回主机与模型", env["status"] == 200 and bool(ej.get("host")) and bool(ej.get("model")),
          f"host={ej.get('host')!r} model={ej.get('model')!r} status={env['status']}")

    roles = get(base, "/api/roles")
    rj = roles["json"] if isinstance(roles["json"], list) else []
    rmap = {r.get("id"): r.get("tools") or [] for r in rj if isinstance(r, dict)}
    expect_roles = {"research", "dataops", "writer", "ops"}
    check("角色集合为 research/dataops/writer/ops", set(rmap) == expect_roles,
          f"roles={sorted(rmap)}")
    check("ops 角色具备 run_shell（服务器命令执行工具）", "run_shell" in rmap.get("ops", []),
          f"ops_tools={rmap.get('ops')}")
    check("dataops 角色具备 sql_query（数据库查询工具）", "sql_query" in rmap.get("dataops", []),
          f"dataops_tools={rmap.get('dataops')}")

    spec = get(base, "/api/openapi.json")
    sj = spec["json"] or {}
    paths = sj.get("paths") or {}
    need_paths = {"/api/health", "/api/env", "/api/roles", "/api/tasks",
                  "/api/tasks/{tid}/events", "/api/tasks/{tid}/stream",
                  "/api/artifacts", "/api/artifacts/{aid}/preview"}
    check("契约含任务/事件/流/产物路径", spec["status"] == 200 and need_paths <= set(paths),
          f"missing={sorted(need_paths - set(paths))} declared={sorted(paths)}")
    task_ops = set((paths.get("/api/tasks") or {}).keys())
    check("任务创建为 POST（本脚本明确不调用）", "post" in task_ops,
          f"/api/tasks methods={sorted(task_ops)}；本校验全程仅 GET")

    tasks = get(base, "/api/tasks?limit=10")
    tj = tasks["json"] if isinstance(tasks["json"], list) else []
    check("任务列表可读且含已完成任务", tasks["status"] == 200 and
          any(t.get("status") == "done" for t in tj if isinstance(t, dict)),
          f"n_tasks={len(tj)} statuses={[t.get('status') for t in tj if isinstance(t, dict)]}")

    # 真实执行证据：取一个 done 任务的事件流，确认存在 tool result
    tool_names: list[str] = []
    evidence_task = None
    for t in tj:
        if isinstance(t, dict) and t.get("status") == "done" and t.get("role") in ("ops", "dataops"):
            ev = get(base, f"/api/tasks/{t['id']}/events?after=0")
            if isinstance(ev["json"], list):
                names = [e.get("data", {}).get("name") for e in ev["json"]
                         if isinstance(e, dict) and e.get("kind") == "tool"
                         and e.get("data", {}).get("stage") == "result"]
                if names:
                    tool_names, evidence_task = names, t["id"]
                    break
    check("历史任务事件含工具真实返回（非占位描述）",
          any(n in ("run_shell", "sql_query") for n in tool_names),
          f"task={evidence_task} tool_results={tool_names[:12]}")

    arts = get(base, "/api/artifacts?limit=5")
    check("产物清单可读", arts["status"] == 200 and isinstance(arts["json"], list),
          f"status={arts['status']} n={len(arts['json']) if isinstance(arts['json'], list) else None}")

    # 反向对照 1：8093 不是本平台，本平台专属健康端点在此必须 404
    not_ours = get(base, "/quant/api/v1/health")
    check("反向对照：本平台健康端点在 8093 返回 404（两服务不同）",
          not_ours["status"] == 404, f"status={not_ours['status']}")
    # 反向对照 2：不存在路径必须 404，证明 200 不是恒真
    missing = get(base, "/definitely-not-an-endpoint")
    check("反向对照：不存在路径返回 404（校验器非恒真）", missing["status"] == 404,
          f"status={missing['status']}")

    result = {
        "ok": not issues,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "base": base,
        "service": "Agent 工作台 · ai-server（内网既有服务，非本平台部署）",
        "method": "只读 GET；未调用 POST /api/tasks 或任何写端点",
        "evidence": {
            "health": hj,
            "env": {k: ej.get(k) for k in ("host", "model", "uptime", "now")},
            "roles": {k: v for k, v in rmap.items()},
            "openapi_paths": sorted(paths),
            "n_tasks": len(tj),
            "task_statuses": [t.get("status") for t in tj if isinstance(t, dict)],
            "tool_result_names": tool_names,
            "evidence_task_id": evidence_task,
            "n_artifacts": len(arts["json"]) if isinstance(arts["json"], list) else None,
        },
        "negative_controls": {
            "/quant/api/v1/health": {"http_status": not_ours["status"]},
            "nonexistent_path": {"http_status": missing["status"]},
        },
        "checks": checks,
        "passed": sum(1 for c in checks if c["passed"]),
        "total": len(checks),
        "issues": issues,
        "scope_note": (
            "本校验只证明『内网存在经 Agent 工作台间接执行 shell/SQL 的通道』及『该服务不是本平台』。"
            "数据库账号、pgvector 与端口清单来自工作台 2026-11-17 既有巡检产物文本，"
            "不是本脚本直连 PostgreSQL 取得；脚本不假设凭据、不建表、不改既有服务配置。"
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WORKBENCH_PROBE: {'PASS' if result['ok'] else 'FAIL'} "
          f"({result['passed']}/{result['total']})  {base}")
    for c in checks:
        print(f"  [{'ok' if c['passed'] else 'XX'}] {c['name']} — {c['detail']}")
    for i in issues:
        print("  ISSUE:", i)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
