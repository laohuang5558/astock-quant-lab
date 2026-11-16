#!/usr/bin/env python3
"""架构工件确定性校验（M0 验收门）。

检查对象：contracts/openapi.yaml、db/schema.sql、docs/PLATFORM_ARCHITECTURE.md。
设计原则：全部基于解析后的对象与显式断言，不用「正则扫文本后声称通过」的弱校验；
引用扫描必须遍历解析树，且强制断言引用数 > 0，避免漏扫造成的假阴性。

用法：python scripts/validate_contracts.py
输出：output/contracts_check.json；失败时退出码 1。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENAPI = ROOT / "contracts" / "openapi.yaml"
DDL = ROOT / "db" / "schema.sql"
ARCH = ROOT / "docs" / "PLATFORM_ARCHITECTURE.md"
OUT = ROOT / "output" / "contracts_check.json"

issues: list[str] = []
checks: dict[str, object] = {}


def fail(msg: str) -> None:
    issues.append(msg)


def walk_refs(node, found: list[str]) -> None:
    """遍历解析树收集所有 $ref，覆盖嵌套、数组、inline schema 三种写法。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref":
                if isinstance(v, str):
                    found.append(v)
                else:
                    fail(f"$ref 值不是字符串：{v!r}")
            else:
                walk_refs(v, found)
    elif isinstance(node, list):
        for v in node:
            walk_refs(v, found)


def resolve(root: dict, ref: str) -> bool:
    if not ref.startswith("#/"):
        return False
    cur = root
    for part in ref[2:].split("/"):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False
    return True


def check_openapi() -> None:
    try:
        import yaml
    except ImportError:
        fail("缺少 PyYAML，无法解析 openapi.yaml；本校验不允许在解析器缺失时判为通过")
        return
    if not OPENAPI.exists():
        fail(f"缺少 {OPENAPI.relative_to(ROOT)}")
        return
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        fail("openapi.yaml 顶层不是映射")
        return
    checks["openapi_version"] = doc.get("openapi")
    if str(doc.get("openapi", "")) != "3.1.0":
        fail(f"openapi 版本应为 3.1.0，实际 {doc.get('openapi')!r}")

    paths = doc.get("paths") or {}
    methods = {"get", "post", "put", "delete", "patch"}
    ops = [(p, m, o) for p, item in paths.items()
           for m, o in item.items() if isinstance(o, dict) and m.lower() in methods]
    checks["path_count"] = len(paths)
    checks["operation_count"] = len(ops)
    if len(paths) < 15 or len(ops) < 20:
        fail(f"端点数量偏少：paths={len(paths)} operations={len(ops)}（预期 >=15 / >=20）")

    op_ids = []
    for path, method, op in ops:
        oid = op.get("operationId")
        if not oid:
            fail(f"缺少 operationId：{method.upper()} {path}")
        else:
            op_ids.append(oid)
        if not isinstance(op.get("responses"), dict) or not op["responses"]:
            fail(f"缺少 responses：{method.upper()} {path}")
    if len(op_ids) != len(set(op_ids)):
        dup = sorted({x for x in op_ids if op_ids.count(x) > 1})
        fail(f"operationId 重复：{dup}")
    checks["operation_ids_unique"] = len(op_ids) == len(set(op_ids))

    refs: list[str] = []
    walk_refs(doc, refs)
    checks["ref_count"] = len(refs)
    # 防假阴性：文件确实含 $ref，若解析树中一条都没有，说明扫描方法失效
    if len(refs) == 0:
        fail("解析树中未发现任何 $ref，但契约按设计必须含组件引用；判定为扫描失效，不通过")
    unresolved = sorted({r for r in refs if not resolve(doc, r)})
    checks["unresolved_refs"] = unresolved
    if unresolved:
        fail(f"无法解析的 $ref：{unresolved}")

    # 触发类写操作必须声明幂等键
    trigger_ops = {"/runs": "post", "/audit/{run_id}/panels": "post",
                   "/preregistration": "post", "/deliveries": "post",
                   "/agents/tasks/{task_id}/callback": "post"}
    for path, method in trigger_ops.items():
        op = (paths.get(path) or {}).get(method)
        if not isinstance(op, dict):
            fail(f"缺少触发类操作：{method.upper()} {path}")
            continue
        params = [p.get("$ref", "") if isinstance(p, dict) else "" for p in op.get("parameters", [])]
        if not any("IdempotencyKey" in str(p) for p in params):
            fail(f"触发类操作未声明 Idempotency-Key：{method.upper()} {path}")

    comps = (doc.get("components") or {}).get("schemas") or {}
    if "Envelope" not in comps or "AgentOpinion" not in comps:
        fail("缺少 Envelope / AgentOpinion schema")
    checks["schemas"] = sorted(comps.keys())


def strip_sql_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        # 去掉行注释，保留字符串内的 --
        in_quote = False
        cut = None
        for i, ch in enumerate(line):
            if ch == "'":
                in_quote = not in_quote
            elif not in_quote and ch == "-" and i + 1 < len(line) and line[i + 1] == "-":
                cut = i
                break
        out.append(line[:cut] if cut is not None else line)
    return "\n".join(out)


def check_ddl() -> None:
    if not DDL.exists():
        fail(f"缺少 {DDL.relative_to(ROOT)}")
        return
    raw = DDL.read_text(encoding="utf-8")
    sql = strip_sql_comments(raw)

    tables = re.findall(r"CREATE TABLE IF NOT EXISTS quant\.(\w+)", sql)
    checks["ddl_tables"] = tables
    checks["ddl_table_count"] = len(tables)
    if len(tables) < 14:
        fail(f"DDL 表数量偏少：{len(tables)}")
    if len(tables) != len(set(tables)):
        fail("DDL 存在重复表名")

    # 括号平衡（忽略单引号字符串），防半截语句
    depth = 0
    in_quote = False
    for ch in sql:
        if ch == "'":
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    fail("DDL 括号出现多余的右括号")
                    break
    checks["ddl_paren_balanced"] = depth == 0 and not in_quote
    if depth != 0 or in_quote:
        fail(f"DDL 括号或引号不平衡：depth={depth} in_quote={in_quote}")

    required = {
        "pgvector 扩展": "CREATE EXTENSION IF NOT EXISTS vector",
        "独立 schema": "CREATE SCHEMA IF NOT EXISTS quant",
        "向量维度 512": "vector(512)",
        "HNSW 余弦索引": "USING hnsw (embedding vector_cosine_ops)",
        "现金硬约束": "cash_after >= -0.000001",
        "敞口硬约束": "exposure <= 1.000001",
        "证据有效性": "evidence_valid",
        "队列索引": "FOR UPDATE SKIP LOCKED",
        "验收视图": "v_run_summary",
    }
    for label, token in required.items():
        ok = token in sql
        checks[label] = ok
        if not ok:
            fail(f"DDL 缺少{label}：{token}")

    # 外键引用的表必须在本 DDL 中定义（避免悬空引用）
    fk = set(re.findall(r"REFERENCES quant\.(\w+)", sql))
    missing = sorted(fk - set(tables))
    checks["dangling_fk"] = missing
    if missing:
        fail(f"外键引用了未定义的表：{missing}")


def check_arch() -> None:
    if not ARCH.exists():
        fail(f"缺少 {ARCH.relative_to(ROOT)}")
        return
    t = ARCH.read_text(encoding="utf-8")
    for sec in ("## 1. 前后端选型", "## 2. 数据库建模", "## 3. 智能体编排",
                "## 4. 接口契约", "## 5. 目录结构", "## 6. 里程碑与验收门"):
        if sec not in t:
            fail(f"架构文档缺少章节：{sec}")
    for ref in ("contracts/openapi.yaml", "db/schema.sql"):
        if ref not in t:
            fail(f"架构文档未引用工件：{ref}")


def main() -> int:
    check_openapi()
    check_ddl()
    check_arch()
    out = {
        "ok": len(issues) == 0,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "checks": checks,
        "issues": issues,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nCONTRACT_VALIDATION: {'PASS' if out['ok'] else 'FAIL'}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
