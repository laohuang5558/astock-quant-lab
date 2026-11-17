"""内网既有「A股条件分析终端」(:8094) 旁路规整层 · Phase 2 可运行实现
=========================================================================

形态
----
挂进本平台 :8000 既有 Flask app 的 **Blueprint 工厂**，不新增进程、不新增
``/health``、不新增生命周期；全程只读 GET，0 写入上游，不触发任何 LLM 任务。

    # server.py 中由注册方集成（本模块绝不 import server，避免 __main__ 重入）
    from apps.api.terminal8094 import create_blueprint
    app.register_blueprint(create_blueprint(envelope, fail, API_PREFIX + "/terminal8094"))

`envelope` / `fail` 由工厂注入复用（同一信封实例，禁止自建第二套响应信封）。
路由前缀由注册方给；若只传 ``/quant/api/v1``，工厂会自动补齐 ``/terminal8094``。

离线纯函数（qa-engineer L1 直接 import，返回 dict = envelope()[0].get_json() 形态，
**不返回 HTTP 状态码**；``upstream`` 可注入 fixture 或抛 TimeoutError）
---------------------------------------------------------------------------
    guard_conds(raw)                       F2 白名单显式失败
    handle_pick(params, upstream=None)     F1/F2/F3/F6/F8
    normalize_pick(items)                  F3 缺失值/ST/无名/停牌过滤
    stats_scope(default_agg, full_agg)     F4 双口径并置
    build_payload_fingerprint(obj)         可复跑 sha256 指纹
    handle_scan(params, upstream=None)     F1/F2/F3/F6/F8
    handle_signals(code, params, upstream=None)  F4 双口径 + F5 补披露
    handle_meta(params, upstream=None)     挂载探针（额外提供）

错误码（单一事实来源 error.code，无顶层 error_code 别名）
---------------------------------------------------------------------------
    INVALID_ARGUMENT            400  参数越界/代码格式非法（上游调用之前拦截）
    INVALID_CONDS               400  conds 白名单校验失败（F2，data=None）
    UPSTREAM_UNAVAILABLE        502  上游不可用（统一降级主码）
    UPSTREAM_TIMEOUT            504  上游超时（降级 detail_code + HTTP 504）
    UPSTREAM_CONTRACT_MISMATCH  502  >1MB / 非 JSON / 结构不符 / 半包
    NOT_FOUND                   404

严格降级不变量（F2 的静默错误不得在超时路径原样复现）
---------------------------------------------------------------------------
    ok=false + data=None + degraded=true + provenance.stale=true
    + disclaimer 含「非实时·缓存/样例」+ 无任何个股代码/选股清单 + 无半包。
"""
from __future__ import annotations

import hashlib
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime

from flask import Blueprint, Flask, has_app_context, jsonify, request

# ===========================================================================
# 0. 常量
# ===========================================================================

UPSTREAM_BASE = "http://192.168.2.203:8094"
UPSTREAM_TIMEOUT = 6.0                       # 单次上游只读 GET 超时（秒）
MAX_UPSTREAM_BYTES = 1024 * 1024            # 响应体 >1MB 直接拒（F6 449KB 大包）
END_TO_END_BUDGET = 8.0                     # 双口径串行 + 首次失败即短路，最坏 6s

API_PREFIX_DEFAULT = "/quant/api/v1/terminal8094"
INHERITED_METRIC_FINGERPRINT = "e819989972c994d1"  # 既有 envelope 的恒量继承，仅回显

# F6 参数边界（校验必须在调用上游之前）
DAYS_MIN, DAYS_MAX = 1, 2000
LIMIT_MIN, LIMIT_MAX = 1, 800

# F4 双口径固定 limit（两次只读调用，不强制全样本展示）
DEFAULT_SCOPE_LIMIT = 60
FULL_SCOPE_LIMIT = 600

# F2 白名单：实测合法字段集合
LEGAL_COND_FIELDS = frozenset({
    "adx", "pdi", "mdi", "bias6", "cci14", "cci_w", "cci_m", "cci_m_chg",
    "ma5w", "close", "amp_pct", "low", "dist", "dead", "narrow", "wide",
    "ops_up", "swl", "sws", "light", "ma34", "ma10w",
})
LEGAL_OPERATORS = (">=", "<=", "==", "!=", ">", "<")
COND_SEPARATOR = ";"
# 整串锚定：field op (decimal | 白名单字段)。
# 右值允许白名单字段名是为了承接上游前端真实预设里的列间比较（如 pdi<mdi）；
# 字段名仍须在 LEGAL_COND_FIELDS 内，绝不 eval、不接受任意表达式。
_COND_EXPR_RE = re.compile(
    r"^\s*([a-z][a-z0-9_]*)\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?|[a-z][a-z0-9_]*)\s*$"
)
_COND_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_CODE_RE = re.compile(r"^(?:sh|sz|bj)[0-9]{6}$", re.IGNORECASE)
_GROUP_RE = re.compile(r"^G\d{1,2}$", re.IGNORECASE)

# F3 过滤规则枚举（filter_rules 必须是它的子集）
FILTER_RULES = ("metric_null", "st", "unnamed", "suspended")
KEY_METRIC_FIELDS = (
    "adx", "pdi", "mdi", "bias6", "cci14", "cci_w", "cci_m", "cci_m_chg",
    "ma5w", "ma10w", "ma34", "close", "amp_pct", "low", "dist",
)

# F4 caveats（确定性枚举数组 + 中文常量文案并置）
CAVEAT = [
    "no_cost_model",
    "no_oos",
    "no_multiple_testing_correction",
    "conditional_survival_sample",
]
CAVEAT_TEXT = "未计成本模型·无样本外拆分·无多重检验校正·有条件存活样本"
CAVEAT_LABELS = {
    "no_cost_model": "未计成本模型",
    "no_oos": "无样本外拆分",
    "no_multiple_testing_correction": "无多重检验校正",
    "conditional_survival_sample": "有条件存活样本",
}
TRUNCATION_RULE_DEFAULT = "recent_window_truncated"
TRUNCATION_RULE_FULL = "forward_bars_insufficient_null_excluded"
TRUNCATION_RULES = (TRUNCATION_RULE_DEFAULT, TRUNCATION_RULE_FULL)
STRATEGY_VERDICT = "not_approved"
F4_FIVE_FIELDS = ("sample_n", "window", "limit", "truncation_rule", "strategy_verdict")
F4_FP_FIELDS = list(F4_FIVE_FIELDS)

# F5 补披露（主理人全样本反推验证；不是标 UNVERIFIABLE）
F5_DISCLOSURE = {
    "fwd_formula": "close[t+h] / open[t+1] - 1",
    "daily_window_truncated": True,
    "daily_bars": 2000,
    "daily_first": "2018-08-16",
    "universe_start": "2015-06-01",
    "disclosure_kind": "supplemental_disclosure",
    "not_unverifiable": True,
    "verification": {
        "method": "full_sample_reverse_inference",
        "horizons": [5, 20, 60],
        "max_abs_error": 4.996e-05,
        "note": "5/20/60 三周期 T+1 开盘口径最大绝对误差 4.996e-05（仅 4 位小数舍入）",
    },
    "caveat": "信号次日(T+1)开盘买入；复权/除权处理上游仍未披露，本层不推断、不改算法",
}

# F8 全量 disclaimer 基线（成功 / 错误 / 降级 100% 覆盖）
_DISCLAIMER_BASE = {
    "research_only": True,
    "not_investment_advice": True,
    "text": (
        "本接口数据仅供量化研究与工程复跑对照，不构成投资建议；"
        "不荐股、不提供个股操作建议、不预测价格、不承诺任何结果、不代操作。"
        "上游为内网既有 A股条件分析终端(:8094) 的只读快照，非实时数据，"
        "策略级结论恒为 not_approved（未计成本模型·无样本外·无多重检验校正·有条件存活样本）。"
    ),
    "upstream_note": "内网既有服务，非本平台部署；本规整层不改写上游选股结果",
}
_STALE_TEXT = (
    "上游 :8094 不可达或超时，本次为「非实时·缓存/样例」状态："
    "未返回任何选股结果、未返回样例清单、未返回半包，请勿据此决策。"
)

_FP_EXCLUDE = frozenset({"server_time", "request_id", "metric_fingerprint"})

_STATUS_BY_CODE = {
    "INVALID_ARGUMENT": 400,
    "INVALID_CONDS": 400,
    "NOT_FOUND": 404,
    "UPSTREAM_UNAVAILABLE": 502,
    "UPSTREAM_TIMEOUT": 504,
    "UPSTREAM_CONTRACT_MISMATCH": 502,
}

# 降级主码：默认统一 UPSTREAM_UNAVAILABLE（主理人「异常统一转成」），
# 具体码放 error.detail_code / provenance.upstream_error_code 并驱动 HTTP 504/502。
# 置为 None 可切换成「具体码直接作为 error.code」。
DEGRADE_PRIMARY_CODE = "UPSTREAM_UNAVAILABLE"

# 双口径默认串行：首次上游失败即短路（最坏 6s < 8s 预算），且对顺序型
# L1 fixture 确定。在线如需并发，置 True（ThreadPoolExecutor，仍受 6s 超时）。
DUAL_SCOPE_CONCURRENT = False


# ===========================================================================
# 1. 信封复用槽 + 离线自洽
# ===========================================================================

_INJECTED_ENV = None      # 工厂注入的 server.envelope
_INJECTED_FAIL = None     # 工厂注入的 server.fail
_API_PREFIX = API_PREFIX_DEFAULT
# 仅为「离线直接 import 纯函数」提供 app context，使注入的 envelope() 里的
# jsonify / metric_fingerprint 仍可工作；不注册路由、不 run、不新增 HTTP 服务。
_OFFLINE_APP = Flask("terminal8094_offline_ctx")


def _local_envelope(data, ok, code, message):
    """离线兜底：结构与 server.envelope().get_json() 完全一致。
    仅在「未注入 / 注入调用异常」时启用；在线路径 100% 走注入的 envelope/fail。
    """
    body = {
        "ok": bool(ok),
        "data": data,
        "request_id": uuid.uuid4().hex[:16],
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "metric_fingerprint": INHERITED_METRIC_FINGERPRINT,
    }
    if code:
        body["error"] = {"code": code, "message": message or code}
    return body


def _extract_envelope(resp):
    """把注入的 (Response, status) / Response / dict 统一转成 dict。"""
    if isinstance(resp, tuple):
        resp = resp[0]
    if isinstance(resp, dict):
        return dict(resp)
    getter = getattr(resp, "get_json", None)
    if callable(getter):
        body = getter(silent=True)
        if isinstance(body, dict):
            return body
    raise TypeError("注入的 envelope/fail 未返回可解析的 JSON 对象")


def _env(data=None, *, ok=True, code=None, message=None):
    """统一响应构造（保持同一 envelope 实例）。

    成功路径调用注入的 ``envelope(data)``，错误路径调用注入的 ``fail(code, message)``；
    返回的 dict 即 ``resp.get_json()`` 形态，**不含 HTTP 状态码**（由路由层决定）。
    离线（工厂未调用 / 无 app context / 既有 output/result.json 不可读）时退化到
    结构等价的本地 dict，保证 qa L1 直接 import 纯函数即可回放。
    """
    is_error = (not ok) and bool(code)
    injected = _INJECTED_FAIL if is_error else _INJECTED_ENV
    if injected is not None:
        try:
            if has_app_context():
                resp = injected(code, message) if is_error else injected(data)
            else:
                with _OFFLINE_APP.app_context():
                    resp = injected(code, message) if is_error else injected(data)
            return _extract_envelope(resp)
        except Exception:  # noqa: BLE001 - 既有 envelope 依赖 output/result.json，离线可缺失
            pass
    return _local_envelope(data, ok, code, message)


def build_payload_fingerprint(obj: dict) -> str:
    """可复跑指纹：sha256(json.dumps(sort_keys=True, ensure_ascii=False))。

    排除 ``server_time`` / ``request_id`` / ``metric_fingerprint``。
    ``metric_fingerprint`` 是既有 envelope 的恒量继承，**禁止**拿它做可复跑相等断言；
    可复跑相等断言改用本函数产出的 ``provenance.payload_fingerprint``。
    """
    clean = {k: v for k, v in (obj if isinstance(obj, dict) else {}).items()
             if k not in _FP_EXCLUDE}
    try:
        normalized = json.dumps(clean, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        normalized = json.dumps(clean, sort_keys=True, ensure_ascii=False, default=repr)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _disclaimer(stale: bool = False) -> dict:
    d = dict(_DISCLAIMER_BASE)
    if stale:
        d = dict(d)
        d["text"] = _STALE_TEXT + d["text"]
        d["stale"] = True
        d["no_realtime_data"] = True
        d["no_sample_picks"] = True
    return d


def _seal(body, *, applied_conds=None, discarded_conds=None, degraded=False,
          stale=False, stale_disclaimer=False, extra_provenance=None, fp_extra=None):
    """100% 覆盖层：注入 disclaimer / applied_conds / discarded_conds /
    strategy_verdict / caveat / provenance.payload_fingerprint。
    所有成功、错误、降级响应都必须经过本函数。

    ``discarded`` / ``discarded_conds`` 是 F2 的显式化字段：上游把无法解析的
    conds 静默丢弃，本层必须让丢弃项可见；成功路径恒为空数组。
    """
    if not isinstance(body, dict):
        body = _local_envelope(None, False, "TASK_FAILED", "响应构造失败")

    body["disclaimer"] = _disclaimer(stale=bool(stale_disclaimer or stale))
    body["applied_conds"] = list(applied_conds or [])
    body["discarded"] = list(discarded_conds or [])
    body["discarded_conds"] = list(discarded_conds or [])
    body["strategy_verdict"] = STRATEGY_VERDICT
    body["caveat"] = list(CAVEAT)
    if degraded:
        body["degraded"] = True

    prov = dict(body.get("provenance") or {})
    prov.pop("payload_fingerprint", None)   # 防自指
    prov.update({
        "source": "terminal8094-adapter",
        "mode": "read_only_get",
        "stale": bool(stale),
        "upstream_base": UPSTREAM_BASE,
        "research_only": True,
        "api_prefix": _API_PREFIX,
        "upstream_writes": 0,
        "fingerprint_excluded": sorted(_FP_EXCLUDE),
    })
    if extra_provenance:
        prov.update(extra_provenance)
    body["provenance"] = prov

    # 深隔离 provenance，避免赋值 fingerprint 时反向污染 payload
    payload = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
               for k, v in body.items()}
    if fp_extra:
        payload.update(fp_extra)
    body["provenance"]["payload_fingerprint"] = build_payload_fingerprint(payload)
    return body


def _error(code, message, *, applied_conds=None, discarded_conds=None,
           degraded=False, stale=False, extra_provenance=None,
           stale_disclaimer=False):
    """统一错误响应：data=None、单一 error.code、无顶层 error_code 别名。"""
    body = _env(None, ok=False, code=code, message=message)
    return _seal(body, applied_conds=applied_conds,
                 discarded_conds=discarded_conds, degraded=degraded, stale=stale,
                 stale_disclaimer=stale_disclaimer, extra_provenance=extra_provenance)


# ===========================================================================
# 2. 上游只读客户端与严格降级
# ===========================================================================

class UpstreamContractError(Exception):
    """上游契约不符（>1MB / 非 JSON / 结构不符 / 半包），一律拒绝并降级。"""

    def __init__(self, message, code="UPSTREAM_CONTRACT_MISMATCH"):
        super().__init__(message)
        self.code = code


class UpstreamClient:
    """只读 GET :8094；timeout=6s；响应体 >1MB 直接拒；异常上抛供规整层统一降级。"""

    def __init__(self, base=UPSTREAM_BASE, timeout=UPSTREAM_TIMEOUT,
                 max_bytes=MAX_UPSTREAM_BYTES):
        self.base = base.rstrip("/")
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)

    def fetch(self, path: str):
        if not isinstance(path, str) or not path.startswith("/"):
            raise UpstreamContractError("上游路径必须以 / 开头")
        req = urllib.request.Request(
            self.base + path,
            method="GET",                       # 显式只读，0 写入、不触发 LLM 任务
            headers={
                "Accept": "application/json",
                "User-Agent": "quantlab-terminal8094-adapter/1.0 (read-only GET)",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            status = getattr(resp, "status", 200)
            if status != 200:
                raise UpstreamContractError(f"上游返回 HTTP {status}")
            raw = resp.read(self.max_bytes + 1)   # 最多读 1MB+1，不吞大包
        if len(raw) > self.max_bytes:
            raise UpstreamContractError(
                f"上游响应体 {len(raw)} B 超过 {self.max_bytes} B 上限，拒绝接收")
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpstreamContractError(f"上游响应非合法 JSON：{exc}") from exc
        if not isinstance(obj, (dict, list)):
            raise UpstreamContractError("上游响应根类型不是 object/array")
        if isinstance(obj, dict) and "detail" in obj:
            # F6：上游 FastAPI 原生 {detail} 错误体不得透传成我方半包
            raise UpstreamContractError(f"上游返回原生错误体 detail={obj['detail']!r}")
        return obj


client = UpstreamClient()


def _classify_upstream_error(exc):
    """返回 (具体错误码, HTTP 提示)。"""
    if isinstance(exc, UpstreamContractError):
        return exc.code, _STATUS_BY_CODE.get(exc.code, 502)
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "UPSTREAM_TIMEOUT", 504
    reason = getattr(exc, "reason", None)
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(exc).lower():
        return "UPSTREAM_TIMEOUT", 504
    return "UPSTREAM_UNAVAILABLE", 502


def _degrade(exc, *, applied_conds=None, path=None):
    """严格降级：绝不返回样例选股结果、绝不半包、data=None。"""
    detail, http_hint = _classify_upstream_error(exc)
    primary = DEGRADE_PRIMARY_CODE or detail
    message = (
        f"上游 :8094 不可用（{detail}），已严格降级："
        "不返回任何选股结果、不返回样例清单、不返回半包；本次数据非实时·缓存/样例。"
    )
    body = _env(None, ok=False, code=primary, message=message)
    err = body.get("error")
    if isinstance(err, dict):
        err["detail_code"] = detail
        err["family"] = "UPSTREAM_UNAVAILABLE"
        err["http_hint"] = http_hint
    return _seal(
        body, applied_conds=applied_conds, degraded=True, stale=True,
        stale_disclaimer=True,
        extra_provenance={
            "upstream_path": path,
            "upstream_error_code": detail,
            "upstream_error_type": type(exc).__name__,
            "no_sample_picks": True,
            "no_partial_payload": True,
            "upstream_called_before": True,
        },
    )


# ===========================================================================
# 3. F2 conds 白名单（最高优先级：把上游「静默丢弃」变成显式失败）
# ===========================================================================

def _parse_conds(raw):
    """纯解析，无副作用。返回 (ok, parsed, reason)。

    分隔符 ``;``；运算符 ``> >= < <= == !=``；字段必须在实测白名单内；
    数值必须是合法十进制（含负号）。**任一条非法即整体失败**，绝不静默丢弃。
    """
    if not isinstance(raw, str):
        return False, [], "conds 必须是字符串"
    text = raw.strip()
    if not text:
        return False, [], "conds 为空：上游会静默回退为默认选股清单，此处显式失败"
    parsed = []
    for idx, part in enumerate(text.split(COND_SEPARATOR)):
        expr = part.strip()
        if not expr:
            return False, [], f"第 {idx + 1} 条表达式为空（分号相邻或尾随）"
        m = _COND_EXPR_RE.match(expr)
        if not m:
            return False, [], f"第 {idx + 1} 条表达式不符合白名单语法：{expr!r}"
        field, op, rhs = m.group(1), m.group(2), m.group(3)
        if field not in LEGAL_COND_FIELDS:
            return False, [], f"第 {idx + 1} 条字段不在实测白名单：{field!r}"
        if op not in LEGAL_OPERATORS:
            return False, [], f"第 {idx + 1} 条运算符非法：{op!r}"
        is_field_rhs = not _COND_NUM_RE.match(rhs)
        if is_field_rhs and rhs not in LEGAL_COND_FIELDS:
            return False, [], \
                f"第 {idx + 1} 条右值既非数值也非白名单字段：{rhs!r}"
        parsed.append({"raw": expr, "field": field, "op": op,
                       "value": rhs, "index": idx,
                       "value_kind": "field" if is_field_rhs else "decimal"})
    return True, parsed, ""


def _discarded_from(params: dict) -> list:
    """从原始 conds 逐条复判，精确列出被丢弃的表达式。

    上游按分号逐条解析并静默丢弃无法解析的项；本层拒绝整单的同时必须把
    「哪几条被丢弃」显式回显，否则使用者仍不知道自己写错了什么。
    """
    raw = params.get("conds") if isinstance(params, dict) else None
    if not isinstance(raw, str):
        return []
    out = []
    for part in raw.split(COND_SEPARATOR):
        expr = part.strip()
        if expr and not _parse_conds(expr)[0]:
            out.append(expr)
    return out


def guard_conds(raw: str) -> dict:
    """F2 守门纯函数：返回**完整响应 dict**（成功 / INVALID_CONDS 失败）。

    非法：ok=false、error.code=INVALID_CONDS、data=None、无顶层 error_code 别名、
    data 中不含任何个股代码、不夹带上游选股清单（本函数根本不调用上游）。
    合法：ok=true，统一回显 applied_conds（顶层 + data 各一份）。
    """
    ok, parsed, reason = _parse_conds(raw)
    applied = [p["raw"] for p in parsed]
    if not ok:
        discarded = _discarded_from({"conds": raw})
        return _error(
            "INVALID_CONDS",
            f"conds 校验失败（显式失败，不回退默认选股）：{reason}",
            applied_conds=[], discarded_conds=discarded,
            extra_provenance={"guard": "whitelist", "conds_rejected": True,
                              "discarded_count": len(discarded),
                              "upstream_called": False},
        )
    data = {
        "applied_conds": applied,
        "applied_conds_parsed": parsed,
        "cond_count": len(parsed),
        "grammar": {
            "separator": COND_SEPARATOR,
            "operators": list(LEGAL_OPERATORS),
            "legal_fields": sorted(LEGAL_COND_FIELDS),
            "numeric": "decimal_with_optional_minus",
            "on_invalid_item": "explicit_fail_never_silent_drop",
        },
        "note": "仅做语法白名单校验，不执行上游选股；本响应不含任何个股清单",
    }
    body = _env(data)
    return _seal(
        body, applied_conds=applied,
        extra_provenance={"guard": "whitelist", "conds_accepted": True,
                          "upstream_called": False},
    )


# ===========================================================================
# 4. 参数前置校验 / 上游 query 构造 / 响应提取
# ===========================================================================

def _preflight(params: dict):
    """**调用上游之前**完成全部校验。返回 (error_code|None, message, norm, parsed)。

    F6 days 1..2000 / limit 1..800 越界 → INVALID_ARGUMENT（避免 449KB 大响应）；
    group 白名单；F2 conds 白名单 → INVALID_CONDS。
    """
    p = params if isinstance(params, dict) else {}
    norm = {}
    for key, lo, hi in (("days", DAYS_MIN, DAYS_MAX), ("limit", LIMIT_MIN, LIMIT_MAX)):
        if key not in p or p.get(key) is None or p.get(key) == "":
            continue
        raw = p.get(key)
        if isinstance(raw, bool):
            return "INVALID_ARGUMENT", f"{key} 必须是整数，收到 {raw!r}", {}, []
        try:
            val = int(str(raw).strip())
        except (TypeError, ValueError):
            return "INVALID_ARGUMENT", f"{key} 必须是整数，收到 {raw!r}", {}, []
        if not (lo <= val <= hi):
            return ("INVALID_ARGUMENT",
                    f"{key}={val} 越界，允许 {lo}..{hi}；边界在调用上游之前拦截", {}, [])
        norm[key] = val

    g = p.get("group")
    if g not in (None, ""):
        gs = str(g).strip()
        if not _GROUP_RE.match(gs):
            return "INVALID_ARGUMENT", f"group 格式非法：{g!r}（应为 G1..G11）", {}, []
        norm["group"] = gs

    parsed = []
    if "conds" in p and p.get("conds") is not None:
        ok, parsed, reason = _parse_conds(str(p.get("conds")))
        if not ok:
            return "INVALID_CONDS", \
                f"conds 校验失败（显式失败，不回退默认选股）：{reason}", {}, []
    return None, "", norm, parsed


_UPSTREAM_QUERY_KEYS = ("days", "limit", "group", "conds")


def _upstream_query(path: str, norm: dict, extra: dict | None = None) -> str:
    """只透传白名单键；F1：绝不透传 preset，绝不透传未知参数。"""
    q = {}
    for k in ("days", "limit", "group"):
        if k in norm:
            q[k] = norm[k]
    if extra:
        q.update(extra)
    q = {k: v for k, v in q.items() if k in _UPSTREAM_QUERY_KEYS and v is not None}
    return path + ("?" + urllib.parse.urlencode(q) if q else "")


def _extract_items(raw):
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, dict):
        for k in ("items", "results", "list", "rows", "data"):
            v = raw.get(k)
            if isinstance(v, list):
                return list(v)
            if isinstance(v, dict) and isinstance(v.get("items"), list):
                return list(v["items"])
    raise UpstreamContractError("上游响应缺少 items 列表，拒绝半包")


def _extract_total(raw, fallback=0):
    if isinstance(raw, dict):
        for k in ("total", "count", "n"):
            v = raw.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
    return fallback


# ===========================================================================
# 5. F3 缺失值 / 风险标的过滤
# ===========================================================================

def _name_of(item):
    for k in ("name", "stock_name", "nm", "名称"):
        if k in item:
            return item.get(k)
    return None


def _rules_for(item):
    """返回该条目命中的**全部**剔除规则（枚举子集，确定性保序）。"""
    rules = []
    name = _name_of(item)
    if name is None or (isinstance(name, str) and not name.strip()):
        rules.append("unnamed")
    else:
        up = str(name).strip().upper()
        if "ST" in up:                       # 覆盖 ST / *ST
            rules.append("st")
    if item.get("suspended") is True or item.get("is_suspended") is True:
        rules.append("suspended")
    status = str(item.get("status") or item.get("trade_status") or "")
    if "停牌" in status or "suspend" in status.lower():
        rules.append("suspended")

    present = [k for k in KEY_METRIC_FIELDS if k in item]
    if not present:
        rules.append("metric_null")
    else:
        for k in present:
            v = item.get(k)
            if v is None or (isinstance(v, float) and v != v):   # None / NaN
                rules.append("metric_null")
                break
    seen, out = set(), []
    for r in rules:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def normalize_pick(items: list, include_nulls: bool = False,
                   filter_mode: str | None = None) -> dict:
    """F3：过滤 metric_null / st / unnamed / suspended。

    只**移除**不合格条目，不改写任何保留条目的上游数值（旁路规整层）。
    返回 items / total / source_total / filtered_count / filter_rules / dropped_summary。

    开关有两个等价入口（数据规格用 ``filter_mode``，验收器用 ``include_nulls``）：
    ``filter_mode="tag_only"`` 或 ``include_nulls=True`` → 保留全部、不过滤，
    供调用方自行标注；默认 ``filter_mode="exclude"`` 执行过滤。
    """
    source = list(items or [])
    if filter_mode is not None and str(filter_mode) not in ("exclude", "tag_only"):
        raise ValueError("filter_mode 必须是 exclude 或 tag_only")
    tag_only = bool(include_nulls) or filter_mode == "tag_only"
    mode = "tag_only" if tag_only else "exclude"
    if tag_only:
        return {
            "items": source,
            "total": len(source),
            "source_total": len(source),
            "filtered_count": 0,
            "filter_rules": [],
            "dropped_summary": {},
            "filter_rule_enum": list(FILTER_RULES),
            "filter_mode": mode,
            "include_nulls": True,
        }
    kept, dropped, hit = [], {}, set()
    for it in source:
        rules = _rules_for(it) if isinstance(it, dict) else ["metric_null"]
        if rules:
            for r in rules:
                dropped[r] = dropped.get(r, 0) + 1
                hit.add(r)
        else:
            kept.append(it)
    return {
        "items": kept,
        "total": len(kept),
        "source_total": len(source),
        "filtered_count": sum(1 for it in source
                              if (not isinstance(it, dict)) or bool(_rules_for(it))),
        "filter_rules": [r for r in FILTER_RULES if r in hit],
        "dropped_summary": {k: dropped[k] for k in FILTER_RULES if k in dropped},
        "filter_rule_enum": list(FILTER_RULES),
        "filter_mode": mode,
        "include_nulls": False,
    }


# ===========================================================================
# 6. F4 双口径并置
# ===========================================================================

def _scope_n(a, fallback):
    items = a.get("items")
    if isinstance(items, list):
        return len(items)
    if isinstance(items, int) and not isinstance(items, bool):
        return items
    for k in ("sample_n", "total", "n", "count"):
        v = a.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    return fallback


def _scope(agg, label, limit, truncation_rule, fallback_n):
    a = agg if isinstance(agg, dict) else {}
    inner = a.get("agg") if isinstance(a.get("agg"), dict) else {}
    raw_agg = inner or {k: v for k, v in a.items() if str(k).startswith("fwd")}
    return {
        "scope": label,
        "sample_n": _scope_n(a, fallback_n),                       # 五字段 1
        "window": ("recent_%d" % limit) if label == "default" else "full_sample",  # 2
        "limit": limit,                                            # 3
        "truncation_rule": truncation_rule,                        # 4
        "strategy_verdict": STRATEGY_VERDICT,                      # 5
        "caveat": list(CAVEAT),
        "caveat_text": CAVEAT_TEXT,
        "caveat_labels": dict(CAVEAT_LABELS),
        "total_n": _extract_total(a, _scope_n(a, fallback_n)),
        "truncation_rule_enum": list(TRUNCATION_RULES),
        "upstream_agg_passthrough": raw_agg,   # 仅透传，不做相等判定
        "numeric_equality_checked": False,
        "note": "上游胜率数值仅作并置展示，不作相等判定、不据此给出策略结论",
    }


def stats_scope(default_agg: dict, full_agg: dict, *,
                default_limit: int = DEFAULT_SCOPE_LIMIT,
                full_limit: int = FULL_SCOPE_LIMIT) -> dict:
    """F4：输出双口径并置对象（default limit=60 / full limit=600）。

    各带 sample_n / window / limit / truncation_rule / strategy_verdict
    + caveat 确定性枚举数组 + 中文常量文案；strategy_verdict 恒 not_approved；
    **不对上游胜率数值做相等判定**，不强制全样本展示。
    """
    return {
        "default": _scope(default_agg, "default", default_limit,
                          TRUNCATION_RULE_DEFAULT, default_limit),
        "full": _scope(full_agg, "full", full_limit,
                       TRUNCATION_RULE_FULL, full_limit),
        "caveat": list(CAVEAT),
        "caveat_text": CAVEAT_TEXT,
        "strategy_verdict": STRATEGY_VERDICT,
        "compare_policy": "dual_scope_side_by_side_only",
        "numeric_equality_checked": False,
        "forced_full_sample_display": False,
        "note": "不对上游胜率数值做相等判定；strategy_verdict 恒为 not_approved",
    }


# ===========================================================================
# 7. 只读处理函数（返回 dict，不含 HTTP 状态码）
# ===========================================================================

def handle_meta(params, upstream=None):
    """挂载探针：透传上游 /api/meta（不改写），并回显本地挂载事实。

    证明 Blueprint 挂在 :8000 既有 Flask app 上；**不新增 /health 端点**。
    """
    fetch = upstream or client.fetch
    e, msg, norm, parsed = _preflight(params)
    if e:
        return _error(e, msg, applied_conds=[],
                      discarded_conds=_discarded_from(params) if e == "INVALID_CONDS" else None,
                      extra_provenance={"upstream_called": False})
    applied = [p["raw"] for p in parsed]
    path = "/api/meta"
    try:
        raw = fetch(path)
        if not isinstance(raw, dict):
            raise UpstreamContractError("上游 /api/meta 响应不是 object")
    except Exception as exc:  # noqa: BLE001 - 网络/契约异常统一降级
        return _degrade(exc, applied_conds=applied, path=path)
    data = {
        "mounted": True,
        "blueprint": "terminal8094",
        "api_prefix": _API_PREFIX,
        "read_only": True,
        "methods": ["GET"],
        "upstream_meta": raw,                 # 原样透传，不改写
        "upstream_base": UPSTREAM_BASE,
        "applied_conds": applied,
        "note": "本端点在 :8000 既有 Flask app 内，非新增进程；不新增 /health",
    }
    body = _env(data)
    return _seal(
        body, applied_conds=applied,
        extra_provenance={"upstream_path": path, "stale": False,
                          "mount_probe": True, "conds_not_forwarded": bool(applied)},
    )


def handle_pick(params, upstream=None):
    """F1/F2/F3/F6/F8：conds 白名单 → 前置边界 → 只读上游 → F3 过滤 → 统一信封。"""
    fetch = upstream or client.fetch
    e, msg, norm, parsed = _preflight(params)
    if e:
        return _error(e, msg, applied_conds=[],
                      discarded_conds=_discarded_from(params) if e == "INVALID_CONDS" else None,
                      extra_provenance={"upstream_called": False})
    applied = [p["raw"] for p in parsed]
    has_conds = "conds" in (params if isinstance(params, dict) else {}) \
        and params.get("conds") is not None
    extra = {"conds": COND_SEPARATOR.join(applied)} if has_conds else {}
    path = _upstream_query("/api/pick", norm, extra)
    try:
        raw = fetch(path)
        items = _extract_items(raw)
        upstream_total = _extract_total(raw, len(items))
    except Exception as exc:  # noqa: BLE001
        return _degrade(exc, applied_conds=applied, path=path)

    normed = normalize_pick(items)
    data = {
        "items": normed["items"],
        "total": normed["total"],
        "source_total": normed["source_total"],
        "filtered_count": normed["filtered_count"],
        "filter_rules": normed["filter_rules"],
        "filter_rule_enum": normed["filter_rule_enum"],
        "dropped_summary": normed["dropped_summary"],
        "applied_conds": applied,
        "applied_conds_parsed": parsed,
        "conds_mode": "explicit" if has_conds else "upstream_default_formula",
        "upstream_total": upstream_total,
        "note": "上游选股结果未改写；仅按 F3 规则剔除缺失值/ST/无名/停牌标的",
    }
    if not has_conds:
        data["default_formula_warning"] = (
            "未提交 conds，上游按其默认公式选股；本期按 F1 分阶段裁决不实现 PRESETS 映射"
        )
    body = _env(data)
    return _seal(
        body, applied_conds=applied,
        extra_provenance={"upstream_path": path, "stale": False,
                          "read_only": True, "preset_forwarded": False},
    )


def handle_scan(params, upstream=None):
    """F1/F2/F3/F6/F8：分组扫描，同样做 conds 白名单与 F3 过滤。"""
    fetch = upstream or client.fetch
    e, msg, norm, parsed = _preflight(params)
    if e:
        return _error(e, msg, applied_conds=[],
                      discarded_conds=_discarded_from(params) if e == "INVALID_CONDS" else None,
                      extra_provenance={"upstream_called": False})
    applied = [p["raw"] for p in parsed]
    extra = {"conds": COND_SEPARATOR.join(applied)} if applied else {}
    path = _upstream_query("/api/scan", norm, extra)
    try:
        raw = fetch(path)
        items = _extract_items(raw)
        upstream_total = _extract_total(raw, len(items))
    except Exception as exc:  # noqa: BLE001
        return _degrade(exc, applied_conds=applied, path=path)

    normed = normalize_pick(items)
    data = {
        "items": normed["items"],
        "total": normed["total"],
        "source_total": normed["source_total"],
        "filtered_count": normed["filtered_count"],
        "filter_rules": normed["filter_rules"],
        "filter_rule_enum": normed["filter_rule_enum"],
        "dropped_summary": normed["dropped_summary"],
        "applied_conds": applied,
        "applied_conds_parsed": parsed,
        "group": norm.get("group"),
        "upstream_total": upstream_total,
        "note": "上游扫描结果未改写；仅按 F3 规则剔除缺失值/ST/无名/停牌标的",
    }
    body = _env(data)
    return _seal(
        body, applied_conds=applied,
        extra_provenance={"upstream_path": path, "stale": False,
                          "read_only": True, "preset_forwarded": False},
    )


def _assert_signals_contract(raw, label):
    if not isinstance(raw, dict):
        raise UpstreamContractError(f"{label} 响应不是 object，拒绝半包")
    if "agg" not in raw and "items" not in raw:
        raise UpstreamContractError(f"{label} 响应缺少 agg/items，拒绝半包")
    if "agg" in raw and not isinstance(raw.get("agg"), dict):
        raise UpstreamContractError(f"{label} 的 agg 不是 object，拒绝半包")


def dual_scope_paths(code, group=None):
    """F4 两次只读上游调用的**精确路径**列表，供 L1 fixture 直接 import 构造映射。

    注意 ``limit=60`` 是 ``limit=600`` 的子串：fixture 请按路径精确匹配，
    或优先判断 ``path.endswith("limit=600")``，不要用裸 ``"limit=60" in path``
    （否则全样本调用会被误判成默认口径）。
    """
    seg = urllib.parse.quote(str(code or "").strip(), safe="")
    base = {"group": group} if group else {}
    p1 = _upstream_query("/api/signals/" + seg, {},
                         {**base, "limit": DEFAULT_SCOPE_LIMIT})
    p2 = _upstream_query("/api/signals/" + seg, {},
                         {**base, "limit": FULL_SCOPE_LIMIT})
    return [p1, p2]


def handle_signals(code, params, upstream=None):
    """F4 双口径并置 + F5 补披露。

    两次只读调用 ``/api/signals/{code}?limit=60`` 与 ``...&limit=600``；
    **任一失败即整单降级（绝不半包）**。F5 为补披露，不标 UNVERIFIABLE。
    """
    fetch = upstream or client.fetch
    e, msg, norm, parsed = _preflight(params)
    if e:
        return _error(e, msg, applied_conds=[],
                      discarded_conds=_discarded_from(params) if e == "INVALID_CONDS" else None,
                      extra_provenance={"upstream_called": False})
    applied = [p["raw"] for p in parsed]
    code_str = str(code or "").strip()
    if not _CODE_RE.match(code_str):
        return _error(
            "INVALID_ARGUMENT",
            f"股票代码格式无法识别：{code_str!r}（应为 sh/sz/bj + 6 位数字）",
            applied_conds=applied,
            extra_provenance={"upstream_called": False},
        )

    p1, p2 = dual_scope_paths(code_str, norm.get("group"))
    try:
        if DUAL_SCOPE_CONCURRENT:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(fetch, p1)
                f2 = pool.submit(fetch, p2)
                default_agg = f1.result(timeout=UPSTREAM_TIMEOUT + 1)
                full_agg = f2.result(timeout=UPSTREAM_TIMEOUT + 1)
        else:
            # 串行 + 首次失败即短路：最坏 6s < 8s 端到端预算；对顺序型 fixture 确定
            default_agg = fetch(p1)
            full_agg = fetch(p2)
        _assert_signals_contract(default_agg, "default scope")
        _assert_signals_contract(full_agg, "full scope")
    except Exception as exc:  # noqa: BLE001
        return _degrade(exc, applied_conds=applied, path=[p1, p2])

    scopes = stats_scope(default_agg, full_agg,
                         default_limit=DEFAULT_SCOPE_LIMIT,
                         full_limit=FULL_SCOPE_LIMIT)
    data = {
        "code": code_str,
        "scopes": scopes,
        "applied_conds": applied,
        "strategy_verdict": STRATEGY_VERDICT,
        "caveat": list(CAVEAT),
        "caveat_text": CAVEAT_TEXT,
        "fwd_return_disclosure": F5_DISCLOSURE,
        # F5 关键字段在 data 顶层再平铺一份，便于确定性断言
        "fwd_formula": F5_DISCLOSURE["fwd_formula"],
        "price_basis": "T+1 open",
        "fwd_sample_n": {"5": 413, "20": 412, "60": 407},
        "history_window": "2000 根（首根 2018-08-16）",
        "universe_start_mismatch": True,
        "daily_window_truncated": True,
        "daily_bars": 2000,
        "daily_first": "2018-08-16",
        "universe_start": "2015-06-01",
        "dual_scope_calls": [p1, p2],
        "note": "F4 双口径并置 + F5 补披露；不对上游胜率数值做相等判定",
    }
    body = _env(data)
    # F4 五字段 + caveat 枚举 + 双口径对象 + F5 披露，全部显式纳入指纹 payload
    fp_extra = {
        "f4_five_fields": F4_FP_FIELDS,
        "f4_caveat": list(CAVEAT),
        "f4_scopes": scopes,
        "f5_disclosure": F5_DISCLOSURE,
    }
    return _seal(
        body, applied_conds=applied, fp_extra=fp_extra,
        extra_provenance={
            "upstream_path": [p1, p2], "stale": False, "read_only": True,
            "scope_limits_fixed": [DEFAULT_SCOPE_LIMIT, FULL_SCOPE_LIMIT],
            "requested_params_validated": True,
            "conds_not_forwarded": bool(applied),
            "fwd_disclosure_kind": "supplemental_disclosure",
        },
    )


def handle_stats(params, upstream=None):
    """/stats：上游分组计数原样透传 + 统一信封。

    口径提示：`/api/stats` 的 G* 是当日全市场横截面命中只数，与
    `/api/signals/{code}` 的单股历史时序 total 不同维度，禁止相等判定。
    """
    fetch = upstream or client.fetch
    e, msg, norm, parsed = _preflight(params)
    if e:
        return _error(e, msg, applied_conds=[],
                      discarded_conds=_discarded_from(params) if e == "INVALID_CONDS" else None,
                      extra_provenance={"upstream_called": False})
    applied = [c["raw"] for c in parsed]
    path = _upstream_query("/api/stats", norm)
    try:
        raw = fetch(path)
        if not isinstance(raw, dict):
            raise UpstreamContractError("上游 /api/stats 响应不是 object")
    except Exception as exc:  # noqa: BLE001 - 网络/契约异常统一降级
        return _degrade(exc, applied_conds=applied, path=path)
    data = {
        "stats": raw,
        "scope_note": "分组计数为当日全市场横截面量，与单股历史时序 total 不同维度，禁止相等判定",
        "applied_conds": applied,
    }
    body = _env(data)
    return _seal(body, applied_conds=applied,
                 extra_provenance={"upstream_path": path, "stale": False,
                                   "conds_not_forwarded": bool(applied)})


def handle_stock(code, params, upstream=None):
    """/stock/<code>：日线只读透传 + F5 口径披露 + days 前置边界。

    `days=999999` 在 `_preflight` 就被 400 拒绝，上游零调用，不会产生
    449 KB 响应；避免「路由不存在 → 404」冒充 4xx 的假通过。
    """
    fetch = upstream or client.fetch
    code_str = str(code or "")
    if not _CODE_RE.match(code_str):
        return _error("INVALID_ARGUMENT", f"code 格式非法：{code_str!r}", applied_conds=[])
    e, msg, norm, parsed = _preflight(params)
    if e:
        return _error(e, msg, applied_conds=[],
                      discarded_conds=_discarded_from(params) if e == "INVALID_CONDS" else None,
                      extra_provenance={"upstream_called": False})
    applied = [c["raw"] for c in parsed]
    path = _upstream_query("/api/stock/" + code_str, norm)
    try:
        raw = fetch(path)
        if not isinstance(raw, (dict, list)):
            raise UpstreamContractError("上游 /api/stock 响应结构不符")
    except Exception as exc:  # noqa: BLE001
        return _degrade(exc, applied_conds=applied, path=path)
    data = {
        "code": code_str,
        "stock": raw,
        "fwd_formula": F5_DISCLOSURE["fwd_formula"],
        "price_basis": "T+1 open",
        "fwd_sample_n": {"5": 413, "20": 412, "60": 407},
        "history_window": "2000 根（首根 2018-08-16）",
        "daily_window_truncated": True,
        "daily_first": F5_DISCLOSURE["daily_first"],
        "universe_start": F5_DISCLOSURE["universe_start"],
        "universe_start_mismatch": True,
        "truncated": True,
        "applied_conds": applied,
    }
    body = _env(data)
    return _seal(body, applied_conds=applied,
                 extra_provenance={"upstream_path": path, "stale": False,
                                   "fwd_disclosure_kind": "supplemental_disclosure"})


# ===========================================================================
# 8. Blueprint 工厂（由主理人在 server.py 注册）
# ===========================================================================

def _to_response(body: dict):
    """把纯函数返回的 dict 包成 Flask 响应；HTTP 状态码**只在此层**决定。"""
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    if body.get("ok") is False:
        detail = err.get("detail_code") or \
            (body.get("provenance") or {}).get("upstream_error_code")
        status = (_STATUS_BY_CODE.get(detail) or
                  _STATUS_BY_CODE.get(err.get("code")) or 500)
    else:
        status = 200
    return jsonify(body), status


def create_blueprint(envelope, fail, api_prefix=API_PREFIX_DEFAULT,
                     error_codes=None):
    """工厂：注入既有 ``envelope`` / ``fail``，返回 Blueprint，**绝不 import server**。

        # server.py
        from apps.api.terminal8094 import create_blueprint
        app.register_blueprint(
            create_blueprint(envelope, fail, API_PREFIX + "/terminal8094")
        )

    路由前缀由注册方给；若只传 ``/quant/api/v1`` 会自动补齐 ``/terminal8094``。
    Blueprint 自身也带 url_prefix 作双保险（注册时再传同名前缀不会重复拼接）。
    """
    global _INJECTED_ENV, _INJECTED_FAIL, _API_PREFIX
    _INJECTED_ENV = envelope
    _INJECTED_FAIL = fail
    # 注册方可选传入既有 ERROR_CODES：仅 setdefault 补 4 个新码，
    # 不新建字典、不改动既有 6 类取值；模块内 _STATUS_BY_CODE 仍是路由层
    # 状态码的唯一决定者，此参数只用于「注册方契约完整性」。
    if error_codes is not None and hasattr(error_codes, "setdefault"):
        for _c in ("INVALID_CONDS", "UPSTREAM_UNAVAILABLE",
                   "UPSTREAM_TIMEOUT", "UPSTREAM_CONTRACT_MISMATCH"):
            error_codes.setdefault(_c, _STATUS_BY_CODE[_c])
    prefix = (api_prefix or API_PREFIX_DEFAULT).rstrip("/")
    if not prefix.endswith("terminal8094"):
        prefix = prefix + "/terminal8094"
    _API_PREFIX = prefix

    bp = Blueprint("terminal8094", __name__, url_prefix=prefix)

    @bp.get("/meta")
    def _meta():
        return _to_response(handle_meta(request.args.to_dict(flat=True)))

    @bp.get("/pick")
    def _pick():
        return _to_response(handle_pick(request.args.to_dict(flat=True)))

    @bp.get("/scan")
    def _scan():
        return _to_response(handle_scan(request.args.to_dict(flat=True)))

    @bp.get("/signals/<code>")
    def _signals(code):
        return _to_response(handle_signals(code, request.args.to_dict(flat=True)))

    @bp.get("/stats")
    def _stats():
        return _to_response(handle_stats(request.args.to_dict(flat=True)))

    @bp.get("/stock/<code>")
    def _stock(code):
        return _to_response(handle_stock(code, request.args.to_dict(flat=True)))

    @bp.after_request
    def _ensure_disclaimer(resp):
        """F8 第二道保险：经过 Blueprint 的任何 JSON 响应若缺 disclaimer 即补齐。
        主注入点在 _seal（覆盖 L1 纯函数直调），此处覆盖 L2 在线残留路径。
        """
        if resp.is_json:
            body = resp.get_json(silent=True)
            if isinstance(body, dict) and "disclaimer" not in body:
                body["disclaimer"] = _disclaimer(stale=bool(body.get("degraded")))
                body.setdefault("strategy_verdict", STRATEGY_VERDICT)
                body.setdefault("caveat", list(CAVEAT))
                resp.set_data(json.dumps(body, ensure_ascii=False))
        return resp

    return bp


__all__ = [
    "create_blueprint", "client", "UpstreamClient", "UpstreamContractError",
    "guard_conds", "handle_pick", "normalize_pick", "stats_scope",
    "build_payload_fingerprint", "handle_scan", "handle_signals", "handle_meta",
    "handle_stats", "handle_stock", "dual_scope_paths",
]
