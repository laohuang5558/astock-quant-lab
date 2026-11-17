"""A股量化研究平台 · 平台化 MVP 后端服务

实现 contracts/openapi.yaml 中 /quant/api/v1 的核心操作。
所有读数来自 output/result.json、output/equity.csv、output/trades.csv 等真实产物，
不内置任何策略数值。触发类操作会真实执行 quantlab 命令行。

运行：python apps/api/server.py  （默认 127.0.0.1:8000）
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "apps" / "web"
OUTPUT = ROOT / "output"
STATE = ROOT / "state"
API_PREFIX = "/quant/api/v1"

app = Flask(__name__, static_folder=None)
_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quant-task")
_TASKS: dict[str, dict] = {}
_IDEMPOTENCY: dict[str, str] = {}

ERROR_CODES = {
    "INVALID_ARGUMENT": 400,
    "NOT_FOUND": 404,
    "IDEMPOTENCY_REQUIRED": 428,
    "CONFLICT": 409,
    "TASK_FAILED": 500,
    "NOT_IMPLEMENTED": 501,
}


# --------------------------------------------------------------------------- 数据读取

def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_result() -> dict:
    rep = _read_json(OUTPUT / "result.json")
    if not isinstance(rep, dict):
        raise RuntimeError("output/result.json 不可读，系统无法提供真实读数")
    return rep


def metric_fingerprint(rep: dict) -> str:
    try:
        sys.path.insert(0, str(ROOT))
        from quantlab.fingerprint import metric_fingerprint as fp
        return fp(rep)
    except Exception:
        return "unavailable"


def envelope(data, ok: bool = True, code: str | None = None, message: str | None = None):
    body = {
        "ok": ok,
        "data": data,
        "request_id": uuid.uuid4().hex[:16],
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "metric_fingerprint": metric_fingerprint(load_result()),
    }
    if code:
        body["error"] = {"code": code, "message": message or code}
    status = 200 if ok else ERROR_CODES.get(code, 500)
    return jsonify(body), status


def fail(code: str, message: str):
    return envelope(None, ok=False, code=code, message=message)


# --------------------------------------------------------------------------- :8094 旁路规整层
# 红线：必须在 envelope / fail / ERROR_CODES / API_PREFIX 定义之后再导入并注册，
# 规整层自身绝不 import server，避免 __main__ 重入与循环依赖。
try:
    import importlib.util as _ilu

    _t8094_path = ROOT / "apps" / "api" / "terminal8094.py"
    _t8094_spec = _ilu.spec_from_file_location("apps_api_terminal8094", _t8094_path)
    _t8094_mod = _ilu.module_from_spec(_t8094_spec)
    sys.modules[_t8094_spec.name] = _t8094_mod
    _t8094_spec.loader.exec_module(_t8094_mod)

    for _code, _status in (
        ("INVALID_CONDS", 400),
        ("UPSTREAM_UNAVAILABLE", 502),
        ("UPSTREAM_TIMEOUT", 504),
        ("UPSTREAM_CONTRACT_MISMATCH", 502),
    ):
        ERROR_CODES.setdefault(_code, _status)

    app.register_blueprint(
        _t8094_mod.create_blueprint(envelope, fail,
                                    API_PREFIX + "/terminal8094", ERROR_CODES)
    )
except Exception as _exc:  # 规整层不可用时不影响主平台既有接口
    print(f"WARN terminal8094 blueprint 未挂载: {_exc}", flush=True)


def read_equity() -> list[dict]:
    path = OUTPUT / "equity.csv"
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = row.get("") or row.get("date") or row.get("Unnamed: 0")
            val = row.get("equity")
            if key and val is not None:
                try:
                    rows.append({"date": key, "equity": float(val)})
                except ValueError:
                    continue
    return rows


def read_trades(limit: int = 200) -> dict:
    path = OUTPUT / "trades.csv"
    if not path.exists():
        return {"columns": [], "rows": [], "total": 0}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        all_rows = [dict(r) for r in reader]
    return {"columns": cols, "rows": all_rows[:limit], "total": len(all_rows)}


def parse_audit_panel() -> dict:
    """从真实审核报告解析委员投票，不手工编造结论。"""
    path = ROOT / "docs" / "AUDIT_PANEL.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    m = re.search(r"##\s*1\s*·\s*专家团与投票([\s\S]*?)(?=\n##\s|\Z)", text)
    opinions = []
    if m:
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 5 or cells[0] in ("委员", "") or set(cells[0]) <= {"-", ":"}:
                continue
            opinions.append({
                "committee": cells[0],
                "agent_id": cells[1].strip("`"),
                "engineering_vote": cells[2],
                "strategy_vote": cells[3],
                "stance": cells[4],
            })
    verdict = ""
    vm = re.search(r"\*\*联合裁决\*\*：(.+)", text)
    if vm:
        verdict = vm.group(1).strip()
    return {"verdict": verdict, "opinions": opinions, "source": "docs/AUDIT_PANEL.md"}


# --------------------------------------------------------------------------- 任务执行

TASK_COMMANDS = {
    "selftest": ([sys.executable, "-m", "quantlab", "selftest"], 180),
    "verify": ([sys.executable, "-m", "quantlab", "verify", "--render"], 180),
    "contracts": ([sys.executable, "scripts/validate_contracts.py"], 120),
    "run": ([sys.executable, "-m", "quantlab", "run"], 600),
}


def _execute(task_id: str, kind: str) -> None:
    cmd, timeout = TASK_COMMANDS[kind]
    with _LOCK:
        _TASKS[task_id]["status"] = "running"
        _TASKS[task_id]["started_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                              text=True, timeout=timeout, encoding="utf-8", errors="replace")
        tail = (proc.stdout or "")[-4000:] + (("\n[stderr]\n" + proc.stderr[-1500:]) if proc.stderr else "")
        with _LOCK:
            t = _TASKS[task_id]
            t["status"] = "succeeded" if proc.returncode == 0 else "failed"
            t["exit_code"] = proc.returncode
            t["log_tail"] = tail.strip()
            t["finished_at"] = datetime.now().isoformat(timespec="seconds")
    except subprocess.TimeoutExpired:
        with _LOCK:
            t = _TASKS[task_id]
            t.update(status="failed", exit_code=-9, log_tail=f"执行超时（{timeout}s）",
                     finished_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as exc:  # noqa: BLE001 - 明确记录失败原因
        with _LOCK:
            t = _TASKS[task_id]
            t.update(status="failed", exit_code=-1, log_tail=f"{type(exc).__name__}: {exc}",
                     finished_at=datetime.now().isoformat(timespec="seconds"))


def create_task(kind: str, idempotency_key: str) -> dict:
    if kind not in TASK_COMMANDS:
        raise ValueError(f"任务类型必须是 {sorted(TASK_COMMANDS)} 之一")
    with _LOCK:
        if idempotency_key in _IDEMPOTENCY:
            return _TASKS[_IDEMPOTENCY[idempotency_key]]
        task_id = "task-" + uuid.uuid4().hex[:12]
        task = {
            "task_id": task_id, "kind": kind, "status": "queued",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "started_at": None, "finished_at": None,
            "exit_code": None, "log_tail": "",
            "command": " ".join(TASK_COMMANDS[kind][0]),
            "idempotency_key": idempotency_key,
        }
        _TASKS[task_id] = task
        _IDEMPOTENCY[idempotency_key] = task_id
    _POOL.submit(_execute, task_id, kind)
    return task


def task_view(task: dict) -> dict:
    return {k: v for k, v in task.items() if k != "idempotency_key"}


def _require_idempotency():
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None, fail("IDEMPOTENCY_REQUIRED", "触发类操作必须携带 Idempotency-Key 请求头")
    return key, None


# --------------------------------------------------------------------------- 静态前端

@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    if filename.startswith("quant/api/"):
        return fail("NOT_FOUND", "未知接口")
    target = WEB_DIR / filename
    if target.is_file():
        return send_from_directory(WEB_DIR, filename)
    return fail("NOT_FOUND", f"静态资源不存在：{filename}")


# --------------------------------------------------------------------------- 契约接口

@app.route(API_PREFIX + "/health")
def health():
    rep = load_result()
    return envelope({
        "status": "ok",
        "service": "astock-quant-platform",
        "version": "0.1.0-mvp",
        "engine": "quantlab (research-only)",
        "result_generated_at": rep.get("meta", {}).get("generated_at"),
        "data_files": {
            "result.json": (OUTPUT / "result.json").is_file(),
            "equity.csv": (OUTPUT / "equity.csv").is_file(),
            "trades.csv": (OUTPUT / "trades.csv").is_file(),
        },
        "note": "数据来自本地真实产物；此服务不含实盘下单与券商接口",
    })


@app.route(API_PREFIX + "/runs")
def list_runs():
    rep = load_result()
    runs = [{
        "run_id": "v3-current", "status": "succeeded",
        "created_at": rep.get("meta", {}).get("generated_at"),
        "config": "config/default.json",
        "sample": rep.get("meta", {}).get("sample"),
        "final_verdict": rep.get("quality_gate", {}).get("final_verdict"),
    }]
    for name, label in (("result_v1_pre_audit.json", "v1-pre-audit"),
                        ("result_v2_post_audit.json", "v2-post-audit")):
        hist = _read_json(OUTPUT / name)
        if hist:
            runs.append({
                "run_id": label, "status": "superseded",
                "created_at": hist.get("meta", {}).get("generated_at"),
                "final_verdict": hist.get("quality_gate", {}).get("final_verdict"),
                "note": "历史作废读数，仅供审核对照",
            })
    with _LOCK:
        for t in _TASKS.values():
            runs.append(task_view(t))
    return envelope({"runs": runs, "total": len(runs)})


@app.route(API_PREFIX + "/runs", methods=["POST"])
def create_run():
    key, err = _require_idempotency()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    kind = str(payload.get("kind", "selftest"))
    if kind not in TASK_COMMANDS:
        return fail("INVALID_ARGUMENT", f"未知任务类型 {kind!r}，可选 {sorted(TASK_COMMANDS)}")
    task = create_task(kind, key)
    return envelope({"run": task_view(task), "accepted": True}, ok=True)


def _resolve_run(run_id: str):
    """把 run_id 解析为（结果字典, 状态）；历史 run 指向其自己的结果文件。"""
    if run_id == "v3-current":
        return load_result(), "succeeded"
    mapping = {
        "v1-pre-audit": "result_v1_pre_audit.json",
        "v2-post-audit": "result_v2_post_audit.json",
    }
    if run_id in mapping:
        hist = _read_json(OUTPUT / mapping[run_id])
        if hist:
            return hist, "superseded"
    with _LOCK:
        if run_id in _TASKS:
            return load_result(), _TASKS[run_id]["status"]
    return None, None


@app.route(API_PREFIX + "/runs/<run_id>")
def get_run(run_id):
    rep, status = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id, "status": status,
                      "generated_at": rep.get("meta", {}).get("generated_at"),
                      "final_verdict": rep.get("quality_gate", {}).get("final_verdict")})


@app.route(API_PREFIX + "/runs/<run_id>/status")
def get_run_status(run_id):
    with _LOCK:
        task = _TASKS.get(run_id)
    if task:
        return envelope({"run_id": run_id, "status": task["status"],
                          "kind": task["kind"], "exit_code": task["exit_code"],
                          "started_at": task["started_at"], "finished_at": task["finished_at"],
                          "log_tail": task["log_tail"][-1200:]})
    rep, status = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id, "status": status, "progress": 1.0 if status == "succeeded" else 0.0})


@app.route(API_PREFIX + "/runs/<run_id>/result")
def get_run_result(run_id):
    rep, status = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id, "status": status, "result": rep})


@app.route(API_PREFIX + "/runs/<run_id>/metrics")
def get_metrics(run_id):
    rep, _ = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id, "metrics": rep.get("performance", {}),
                     "sample": rep.get("meta", {}).get("sample")})


@app.route(API_PREFIX + "/runs/<run_id>/equity")
def get_equity(run_id):
    rep, _ = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    rows = read_equity()
    step = max(1, len(rows) // 800)
    return envelope({"run_id": run_id, "points": len(rows), "step": step,
                     "series": rows[::step] + ([rows[-1]] if rows and rows[-1] is not rows[::step][-1] else [])})


@app.route(API_PREFIX + "/runs/<run_id>/trades")
def get_trades(run_id):
    rep, _ = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    limit = min(500, max(1, int(request.args.get("limit", 200))))
    return envelope({"run_id": run_id, **read_trades(limit)})


@app.route(API_PREFIX + "/runs/<run_id>/factors")
def get_factors(run_id):
    rep, _ = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id, **rep.get("factor_research", {})})


@app.route(API_PREFIX + "/runs/<run_id>/overfit")
def get_overfit(run_id):
    rep, _ = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id, **rep.get("overfit", {})})


@app.route(API_PREFIX + "/runs/<run_id>/quality-gate")
def get_quality_gate(run_id):
    rep, _ = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id, **rep.get("quality_gate", {})})


@app.route(API_PREFIX + "/verify/<run_id>")
def get_verify(run_id):
    rep, _ = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id,
                     "verify": _read_json(OUTPUT / "verify.json"),
                     "contracts": _read_json(OUTPUT / "contracts_check.json")})


@app.route(API_PREFIX + "/audit/<run_id>/opinions")
def get_audit_opinions(run_id):
    rep, _ = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id, **parse_audit_panel(),
                     "audit_evidence": rep.get("audit", {})})


@app.route(API_PREFIX + "/audit/<run_id>/panels", methods=["POST"])
def create_panel(run_id):
    key, err = _require_idempotency()
    if err:
        return err
    rep, _ = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id, "panel_id": f"panel-{key[:8]}",
                     "status": "recorded", "note": "只读记录，不新增或改写委员意见"})


@app.route(API_PREFIX + "/preregistration")
def get_preregistration():
    rep = load_result()
    return envelope({"state": _read_json(STATE / "research_state.json"),
                     "declared": rep.get("meta", {}).get("preregistration"),
                     "oos_state": rep.get("audit", {}).get("oos_state")})


@app.route(API_PREFIX + "/preregistration", methods=["POST"])
def set_preregistration():
    _, err = _require_idempotency()
    if err:
        return err
    st = _read_json(STATE / "research_state.json", {}) or {}
    preg = st.get("preregistration") or {}
    declared = load_result().get("meta", {}).get("preregistration") or {}
    if preg.get("frozen") is True or declared.get("frozen") is True:
        return fail("CONFLICT", "预注册假设已冻结，本平台不允许在线改写；须走 quantlab preregister --force 并递增样本外使用次数")
    return fail("NOT_IMPLEMENTED", "非冻结状态的写入路径尚未启用")


@app.route(API_PREFIX + "/reports/<run_id>/design")
def get_design_report(run_id):
    rep, _ = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id, "format": "html",
                     "url": "/reports/design.html",
                     "fingerprint": metric_fingerprint(rep),
                     "title": rep.get("meta", {}).get("title")})


@app.route(API_PREFIX + "/reports/<run_id>/audit")
def get_audit_report(run_id):
    rep, _ = _resolve_run(run_id)
    if rep is None:
        return fail("NOT_FOUND", f"运行不存在：{run_id}")
    return envelope({"run_id": run_id, "format": "html",
                     "url": "/reports/audit.html",
                     "fingerprint": metric_fingerprint(rep)})


@app.route("/reports/<name>.html")
def serve_report(name):
    candidates = [ROOT.parent / f"astock-quant-{name}-report.html",
                  ROOT / "docs" / f"{name.upper()}_REPORT.html"]
    for p in candidates:
        if p.is_file():
            return Response(p.read_bytes(), mimetype="text/html; charset=utf-8")
    return fail("NOT_FOUND", f"报告文件不存在：{name}")


@app.route(API_PREFIX + "/deliveries")
def list_deliveries():
    return envelope({"deliveries": _read_json(OUTPUT / "delivery_report.json"),
                     "acceptance_url": "http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/index.html"})


@app.route(API_PREFIX + "/deliveries", methods=["POST"])
def create_delivery():
    key, err = _require_idempotency()
    if err:
        return err
    script = ROOT / "deploy" / "publish_app.py"
    if not script.is_file():
        return fail("NOT_IMPLEMENTED", "发布脚本不存在")
    task = create_task("verify", f"delivery-{key}")
    return envelope({"accepted": True, "run": task_view(task),
                     "note": "交付重发布前先执行一致性校验"})


@app.route(API_PREFIX + "/agents/tasks")
def list_agent_tasks():
    with _LOCK:
        tasks = [task_view(t) for t in _TASKS.values()]
    return envelope({"tasks": tasks, "total": len(tasks),
                     "orchestration": "DAG 9 阶段；委员只读，无 evidence_path 的意见不计票"})


@app.route(API_PREFIX + "/agents/tasks/<task_id>/callback", methods=["POST"])
def agent_callback(task_id):
    _, err = _require_idempotency()
    if err:
        return err
    with _LOCK:
        if task_id not in _TASKS:
            return fail("NOT_FOUND", f"任务不存在：{task_id}")
    payload = request.get_json(silent=True) or {}
    evidence = payload.get("evidence_path")
    return envelope({"task_id": task_id, "accepted": bool(evidence),
                     "note": "无 evidence_path 的回调不计票" if not evidence else "已登记证据路径"})


@app.errorhandler(404)
def not_found(_e):
    return fail("NOT_FOUND", "资源或接口不存在")


@app.errorhandler(500)
def server_error(_e):
    return fail("TASK_FAILED", "服务内部错误，详见服务端日志")


def main():
    port = int(os.environ.get("QUANT_PORT", "8000"))
    host = os.environ.get("QUANT_HOST", "127.0.0.1")
    print(f"START quant-platform  http://{host}:{port}/   (API {API_PREFIX})", flush=True)
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
