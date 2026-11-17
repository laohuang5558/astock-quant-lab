#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""":8094 规整层（apps/api/terminal8094.py）双层验收校验器 · Phase 3（最终约束版）。

作者：凌无缺（测试验收工程师）。唯一负责本文件；不改 apps/api/* 或任何其他文件。

────────────────────────────────────────────────────────────────────────
设计纪律
────────────────────────────────────────────────────────────────────────
【双层】
  L1 离线确定性层：直接 import 纯函数 + fixture 回放，断言**输出 dict 的字段**，
      不判 HTTP 状态码（主理人第一条裁决）。fixture 一律由
      output/terminal_8094_probe.json 的 evidence 真实读数构造，禁止编造胜率/样本数。
  L2 在线契约层：脚本内**自建独立 Flask app**（自定义等价 envelope/fail/
      ERROR_CODES/API_PREFIX，绝不 import apps/api/server.py 避免循环），用
      create_blueprint(...) 注册后以 app.test_client() 打真实 HTTP 请求；
      上游用可计数 fake upstream 注入，保证离线确定性。
      layers.l2.upstream_mode = fixture-replay | live；
      3s 内 http://192.168.2.203:8094/api/meta 可达则额外跑 live 冒烟，不可达不判 FAIL。

【形态归一（并行开发期接口对齐，不放水）】
  后端可能返回「完整信封 dict」或「(payload, error_code) 元组」；签名可能是
  2/4 参；类名可能是 UpstreamError / UpstreamContractError。归一只作用于
  **字段抽取位置与调用方式**，绝不改变判定标准。检测到的形态写入
  evidence.interface_variant；相对最终契约的差距写入 evidence.contract_gaps。

【指纹陷阱】
  metric_fingerprint == 'e819989972c994d1' 是继承恒量，只用于「继承能力自检」；
  恒量两次相等是平凡成立，**禁止**用作可复跑相等断言。可复跑由
  provenance.payload_fingerprint（连跑 ≥10 次全等）承担，并断言
  payload_fingerprint != 继承恒量 + build_payload_fingerprint 对改 caveat 必须不等。

【显式反 500】
  L2 每条错误响应断言 status 为预期 4xx/502 且**显式 status != 500**，
  detail 单列 not_500=。反向对照里构造「其余字段全部合规、唯独 status=500」的
  响应跑同一判定函数，必须判 FAIL —— 证明该断言不是被其他条件恒真带过。

【反向对照（成对：正常→PASS / 注入→必须 FAIL）】
  NC1 L2 上游不可达；NC2 fixture 篡改/缺失；NC3 恒量冒充可复跑判据；
  NC4 --negative-control 三缺陷注入（错误码未注册→500 / conds 非法却返回默认
  清单 / payload_fingerprint 用 metric_fingerprint 代替），
  negative_controls[] 记录 expected_fail / observed_fail。

【import 失败或函数缺失】明确判 FAIL 并写出缺失项，禁止静默跳过或吞异常算 PASS。

────────────────────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────────────────────
  python scripts/validate_adapter.py                  # 主流程，全 PASS 退出码 0
  python scripts/validate_adapter.py --base http://127.0.0.1:8000
  python scripts/validate_adapter.py --upstream-base http://127.0.0.1:59999
                                                     # 反向对照 A：L2 必须 FAIL、退出码 1
  python scripts/validate_adapter.py --self-test-negative
                                                     # 反向对照 C：F2 断言组出现 FAIL、退出码 1
  python scripts/validate_adapter.py --negative-control
                                                     # 三缺陷注入自检（shadow probe，不改主结果）
  可选环境变量 TERMINAL8094_MODULE_DIR：覆盖被测模块加载目录（离线自测注入点）。

产物 output/terminal8094_adapter_check.json：
  ok/passed/total/failed/skipped/layers/checks[]/negative_controls/evidence/
  base/target/method/check_time/params/contract_advisories
  checks[] 每条 name/ok/detail/layer/status/assert_object?
  （scripts/build_web_snapshot.py 的 terminal_8094() 硬依赖 ok/passed/total/layers/negative_controls）
全程只读 GET，0 写入上游。
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import inspect
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "terminal8094_adapter_check.json"
PROBE_JSON = ROOT / "output" / "terminal_8094_probe.json"
DEFAULT_BASE = "http://127.0.0.1:8000"
UPSTREAM_LIVE = "http://192.168.2.203:8094"
API_PREFIX = "/quant/api/v1"
T8094_PREFIX = API_PREFIX + "/terminal8094"

INHERITED_FINGERPRINT = "e819989972c994d1"
EXCLUDE_KEYS = frozenset({"server_time", "request_id", "metric_fingerprint"})
CODE_RE = re.compile(r"(?:sz|sh|bj)\d{6}", re.IGNORECASE)
BANNED_WORDS = ("操盘", "买卖点", "目标价", "收益承诺")
NOT_ADVICE = "不构成投资建议"
NON_REALTIME = "非实时"
FILTER_RULE_ENUM = frozenset({"metric_null", "st", "unnamed", "suspended"})
CAVEAT_EXPECTED = {"no_cost_model", "no_oos", "no_multiple_testing_correction",
                   "conditional_survival_sample"}

# 平台既有 envelope()/fail() 的 6 类错误码（不含规整层新增码）——故意不预注册，
# 用于验证「未注册新码会静默落 500」是否可被检出。
PLATFORM_ERROR_CODES = {
    "INVALID_ARGUMENT": 400, "NOT_FOUND": 404, "IDEMPOTENCY_REQUIRED": 428,
    "CONFLICT": 409, "TASK_FAILED": 500, "NOT_IMPLEMENTED": 501,
}

REQUIRED_FUNCS = (
    "guard_conds", "handle_pick", "normalize_pick", "stats_scope",
    "build_payload_fingerprint", "handle_scan", "handle_signals", "create_blueprint",
)
REQUIRED_EXPORTS = REQUIRED_FUNCS + ("client.fetch",)

INVALID_CONDS = ["1/0", "__import__('os').getcwd()", "cci_w", "", "close>0 or 1==1"]
EXTRA_INVALID_CONDS = ["cci_w>99,cci_m>99", "cci_w>99 AND cci_m>99",
                       "cci_w>>99", "unknown_field>1"]
GUARD_NEGATIVE = ["1/0", "__import__('os').getcwd()", "cci_w",
                  "adx>55,pdi<mdi", "cci_w>99 and cci_m>99"]
GUARD_POSITIVE = "adx>55;pdi<mdi;bias6<-3;cci14>=-140;cci14<-100"
VALID_CONDS = "cci_w>99"
SIGNAL_CODE = "sz300398"


# ══════════════════════════════════════════════════════════════════════════
# 0. 通用工具
# ══════════════════════════════════════════════════════════════════════════

def jtext(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(obj)


def code_hits(data) -> int:
    return len(CODE_RE.findall(jtext(data)))


def find_key(obj, key, depth=0):
    if depth > 12:
        return None
    if isinstance(obj, dict):
        v = obj.get(key)
        if isinstance(v, dict):
            return v
        for val in obj.values():
            got = find_key(val, key, depth + 1)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for val in obj[:200]:
            got = find_key(val, key, depth + 1)
            if got is not None:
                return got
    return None


def collect_objects(obj, predicate, depth=0, out=None):
    if out is None:
        out = []
    if depth > 12:
        return out
    if isinstance(obj, dict):
        try:
            if predicate(obj):
                out.append(obj)
        except Exception:
            pass
        for val in obj.values():
            collect_objects(val, predicate, depth + 1, out)
    elif isinstance(obj, list):
        for val in obj[:500]:
            collect_objects(val, predicate, depth + 1, out)
    return out


def diff_fields(a, b, exclude=EXCLUDE_KEYS, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            if k in exclude:
                continue
            if k not in a or k not in b:
                out.append("%s.%s: missing(%s/%s)" % (path, k, k in a, k in b))
                continue
            out.extend(diff_fields(a[k], b[k], exclude, "%s.%s" % (path, k)))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return ["%s: len %d != %d" % (path, len(a), len(b))]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(diff_fields(x, y, exclude, "%s[%d]" % (path, i)))
        return out
    if a != b:
        return ["%s: %r != %r" % (path, a, b)]
    return []


def safe_call(fn, *args, **kwargs):
    """异常不吞：明确返回 (None, err)，由调用方判 FAIL。"""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:  # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, exc)


def error_code(resp):
    if not isinstance(resp, dict):
        return None
    err = resp.get("error")
    return err.get("code") if isinstance(err, dict) else None


def norm_result(r):
    """形态归一：dict 信封 / (payload, error_code) 元组 → (payload_dict, ec, variant)。"""
    if isinstance(r, tuple) and len(r) == 2 and isinstance(r[0], dict):
        return r[0], r[1], "tuple(payload,error_code)"
    if isinstance(r, dict):
        return r, error_code(r), "dict-envelope"
    return {}, None, "unknown:%s" % type(r).__name__


def disclaimer_ok(resp):
    d = find_key(resp, "disclaimer")
    if d is None:
        return False, "disclaimer 缺失"
    if d.get("research_only") is not True:
        return False, "research_only != True"
    if NOT_ADVICE not in jtext(d):
        return False, "未命中「%s」" % NOT_ADVICE
    return True, "research_only=True + 「%s」" % NOT_ADVICE


def banned_count(resp):
    t = jtext(resp)
    return sum(t.count(w) for w in BANNED_WORDS)


def call_stats_scope(mod, d, f):
    """签名适配：4 位置参 → 2 位置参+关键字 → 2 位置参。记录采用形态。"""
    fn = mod.stats_scope
    for label, args, kw in (
        ("4-positional", (d, f, 60, 600), {}),
        ("2+kwargs", (d, f), {"default_limit": 60, "full_limit": 600}),
        ("2-positional", (d, f), {}),
    ):
        r, err = safe_call(fn, *args, **kw)
        if err is None:
            return r, label
    return None, "all-signatures-failed"


def call_blueprint(mod, envelope, fail, prefix, codes):
    """工厂适配：4 参（error_codes）优先，3 参回退；记录是否支持 error_codes。"""
    r, err = safe_call(mod.create_blueprint, envelope, fail, prefix, codes)
    if err is None and r is not None:
        return r, "error_codes-supported"
    r2, err2 = safe_call(mod.create_blueprint, envelope, fail, prefix)
    if err2 is None and r2 is not None:
        return r2, "3-arg-fallback(error_codes-unsupported)"
    return None, "create_blueprint-failed: %s | %s" % (err, err2)


class CountingUpstream:
    """可计数 fake upstream：兼容模块级 client.fetch / __call__，可抛异常触发降级。"""

    def __init__(self, payload, raises=None, label="fake"):
        self.payload = payload
        self.raises = raises
        self.label = label
        self.calls = []

    def _hit(self, *args, **kwargs):
        self.calls.append(str(args[0]) if args else "()")
        if self.raises is not None:
            raise self.raises
        return copy.deepcopy(self.payload)

    def __call__(self, *a, **k):
        return self._hit(*a, **k)

    def fetch(self, *a, **k):
        return self._hit(*a, **k)

    def get(self, *a, **k):
        return self._hit(*a, **k)


class SpyUpstream(CountingUpstream):
    """L1 回放用（语义同 CountingUpstream，保留命名以兼容既有判据）。"""
    pass


# ══════════════════════════════════════════════════════════════════════════
# 1. fixture：一律由 output/terminal_8094_probe.json 的 evidence 真实读数构造
# ══════════════════════════════════════════════════════════════════════════

def load_probe_evidence():
    if not PROBE_JSON.is_file():
        raise RuntimeError("上游体检产物缺失：%s" % PROBE_JSON)
    doc = json.loads(PROBE_JSON.read_text(encoding="utf-8"))
    return doc.get("evidence", {})


try:
    EV = load_probe_evidence()
    EV_ERROR = None
except Exception as exc:  # noqa: BLE001 - 明确记录，绝不静默算 PASS
    EV, EV_ERROR = {}, "%s: %s" % (type(exc).__name__, exc)

DEFAULT_AGG = EV.get("agg_probe", {}).get("limit_default", {}).get("agg", {})
FULL_AGG = EV.get("agg_probe", {}).get("limit_600", {}).get("agg", {})
DEFAULT_ITEMS = EV.get("agg_probe", {}).get("limit_default", {}).get("items")
FULL_TOTAL = EV.get("agg_probe", {}).get("limit_600", {}).get("total")
FWD = EV.get("fwd_recompute", {})
NULL_EXAMPLES = EV.get("pick_quality", {}).get("null_examples", [])
ST_EXAMPLES = EV.get("pick_quality", {}).get("st_examples", [])
G7_EXAMPLES = EV.get("g7_quality", {}).get("examples", [])
VALID_FIRST = {}
try:
    VALID_FIRST = json.loads(EV.get("conds_probe", {}).get("valid_first", "{}"))
except Exception:
    VALID_FIRST = {}
DEFAULT_FIRST = {}
try:
    DEFAULT_FIRST = json.loads(EV.get("preset_probe", {}).get("default_first", "{}"))
except Exception:
    DEFAULT_FIRST = {}


def _normal_item(seed, i):
    """由真实读数 sh600323 变形生成正常标的（数值来自 evidence，不编造胜率/样本数）。"""
    x = dict(seed)
    x["code"] = "sh60%04d" % (4000 + i)
    x["name"] = "研究标的%02d" % i
    return x


def build_pick_items():
    """F3 fixture：真实读数中的 null / ST / 无名标的 + 正常标的。

    显式包含 evidence.pick_quality.null_examples[0] = sz000851（*ST高鸿），
    其 cci14=0.0 / bias6=0.0 是 data-engineer 点名的**伪零哨兵**：
    只按 `is None` 过滤会漏掉它，故单列一条「必须被排除」断言。
    """
    items = []
    for x in NULL_EXAMPLES[:2]:
        y = dict(x)
        items.append(y)
    for x in ST_EXAMPLES[:3]:
        y = dict(x)
        # 避免与 null 样例重复计数：ST 样例若有 null 指标，补成有效值
        for k in ("adx", "pdi", "mdi"):
            if y.get(k) is None:
                y[k] = 20.0
        items.append(y)
    for x in G7_EXAMPLES:
        y = dict(x)
        y.setdefault("adx", 20.0)
        y.setdefault("pdi", 15.0)
        y.setdefault("mdi", 12.0)
        items.append(y)
    n_bad = len(items)
    seed = VALID_FIRST or DEFAULT_FIRST or {"code": "sh600323", "name": "瀚蓝环境",
                                            "adx": 14.5, "pdi": 16.3, "mdi": 12.6,
                                            "cci14": 75.3, "bias6": 1.46, "close": 31.31}
    for i in range(20):
        items.append(_normal_item(seed, i))
    return items, n_bad


PICK_ITEMS, PICK_BAD_COUNT = build_pick_items()
FIX_PICK = {"items": copy.deepcopy(PICK_ITEMS), "total": len(PICK_ITEMS)}
FIX_SCAN = {"items": [dict(x) for x in G7_EXAMPLES] or
            [{"code": "sz300326", "name": "研究A", "group": "G7"}],
            "total": len(G7_EXAMPLES) or 1, "group": "G7"}
SIGNALS_FIX = {
    "code": SIGNAL_CODE, "group": "G1", "name": "真实读数合成信号",
    "total": FULL_TOTAL or 515,
    "items": FWD.get("per_horizon", {}).get("5", {}) and [] or [],
    "agg": copy.deepcopy(FULL_AGG),
    "daily": [],
    "meta": {"universe_start": FWD.get("universe_start") or "2015-06-01"},
    "fwd_formula": FWD.get("verified_formula") or "close[t+h]/open[t+1]-1",
    "daily_first": FWD.get("daily_first"),
    "daily_last": FWD.get("daily_last"),
    "daily_len": FWD.get("daily_len"),
    "universe_start": FWD.get("universe_start") or "2015-06-01",
}


# ══════════════════════════════════════════════════════════════════════════
# 2. 模块加载
# ══════════════════════════════════════════════════════════════════════════

def resolve_module_path():
    env_dir = os.environ.get("TERMINAL8094_MODULE_DIR")
    if env_dir:
        for p in (Path(env_dir) / "terminal8094.py",
                  Path(env_dir) / "apps" / "api" / "terminal8094.py"):
            if p.is_file():
                return p
    return ROOT / "apps" / "api" / "terminal8094.py"


def load_module():
    path = resolve_module_path()
    for p in (str(ROOT), str(ROOT / "apps" / "api")):
        if p not in sys.path:
            sys.path.insert(0, p)
    if not path.is_file():
        return None, list(REQUIRED_EXPORTS), "模块文件不存在: %s" % path
    try:
        spec = importlib.util.spec_from_file_location("t8094_adapter_under_test", str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001
        return None, list(REQUIRED_EXPORTS), "%s: %s @ %s" % (type(exc).__name__, exc, path)
    missing = [f for f in REQUIRED_FUNCS if not callable(getattr(mod, f, None))]
    client = getattr(mod, "client", None)
    if client is None or not callable(getattr(client, "fetch", None)):
        missing.append("client.fetch")
    return mod, missing, None


def upstream_error_class(mod):
    for name in ("UpstreamError", "UpstreamContractError"):
        cls = getattr(mod, name, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            return cls
    return TimeoutError


# ══════════════════════════════════════════════════════════════════════════
# 3. Probe
# ══════════════════════════════════════════════════════════════════════════

class Probe:
    def __init__(self):
        self.checks = []
        self.negative_controls = []
        self.evidence = {}
        self.all_responses = []

    def check(self, name, ok, detail="", layer="L1", status=None, assert_object=None):
        if status is None:
            status = "PASS" if ok else "FAIL"
        item = {"name": name, "ok": bool(ok), "detail": str(detail),
                "layer": layer, "status": status}
        if assert_object:
            item["assert_object"] = assert_object
        self.checks.append(item)

    def nc(self, name, ok, detail, expected_fail=True, observed_fail=None):
        item = {"name": name, "ok": bool(ok), "detail": str(detail),
                "expected_fail": bool(expected_fail),
                "observed_fail": bool(ok) if observed_fail is None else bool(observed_fail)}
        self.negative_controls.append(item)

    def record(self, label, resp):
        self.all_responses.append((label, resp))

    def layer_stat(self, layer):
        cs = [c for c in self.checks if c["layer"] == layer]
        active = [c for c in cs if c["status"] != "SKIPPED"]
        skipped = len(cs) - len(active)
        passed = sum(1 for c in active if c["status"] == "PASS")
        if skipped and not active:
            state, ok = "SKIPPED", None
        elif passed == len(active) and active:
            state, ok = "PASS", True
        else:
            state, ok = "FAIL", False
        return {"status": state, "ok": ok, "passed": passed, "total": len(active),
                "failed": len(active) - passed, "skipped": skipped}

    @property
    def active_checks(self):
        return [c for c in self.checks if c["status"] != "SKIPPED"]

    @property
    def passed(self):
        return sum(1 for c in self.active_checks if c["status"] == "PASS")

    @property
    def total(self):
        return len(self.active_checks)

    @property
    def failed(self):
        return self.total - self.passed

    @property
    def skipped(self):
        return sum(1 for c in self.checks if c["status"] == "SKIPPED")


# ══════════════════════════════════════════════════════════════════════════
# 4. 共用判定函数（主流程与反向对照必须用同一函数 —— 成对证明非恒真）
# ══════════════════════════════════════════════════════════════════════════

def judge_invalid_conds(resp):
    """L1 无效 conds 字段判定（不判 HTTP 状态码）。"""
    if not isinstance(resp, dict):
        return False, "resp 非 dict"
    ok_false = resp.get("ok") is False
    code_ok = error_code(resp) == "INVALID_CONDS"
    data = resp.get("data")
    data_none = data is None
    no_code = code_hits(data) == 0
    no_alias = "error_code" not in resp
    disc, dd = disclaimer_ok(resp)
    ok = ok_false and code_ok and data_none and no_code and no_alias and disc
    return ok, ("ok_false=%s code=%r data_is_None=%s code_hits=%d no_error_code_alias=%s "
                "disclaimer=%s(%s)" % (ok_false, error_code(resp), data_none,
                                       code_hits(data), no_alias, disc, dd))


def judge_valid_conds(resp, cond):
    if not isinstance(resp, dict):
        return False, "resp 非 dict"
    ok_true = resp.get("ok") is True
    t = jtext(resp)
    echo = "applied_conds" in t and cond in t
    return (ok_true and echo), "ok_true=%s applied_conds 回显=%s" % (ok_true, echo)


def judge_http_invalid_conds(status, body):
    """L2 HTTP 判定：显式断言 status == 400 且 status != 500（反 500 静默通过）。"""
    if not isinstance(body, dict):
        return False, "body 非 dict"
    not_500 = (status != 500)                       # ← 独立布尔子条件，单列 detail
    is_400 = (status == 400)
    ok_false = body.get("ok") is False
    code_ok = error_code(body) == "INVALID_CONDS"
    data_none = body.get("data") is None
    no_code = code_hits(body.get("data")) == 0
    no_sz000851 = "sz000851" not in jtext(body)
    no_alias = "error_code" not in body
    ok = not_500 and is_400 and ok_false and code_ok and data_none and no_code \
        and no_sz000851 and no_alias
    return ok, ("status=%s not_500=%s is_400=%s ok_false=%s code=%r data_is_None=%s "
                "code_hits=%d no_sz000851=%s no_error_code_alias=%s"
                % (status, not_500, is_400, ok_false, error_code(body), data_none,
                   code_hits(body.get("data")), no_sz000851, no_alias))


def judge_http_error(status, body, expect_status, expect_code):
    """L2 通用错误判定：status 精确匹配、显式 != 500、错误码、data、无顶层别名。"""
    not_500 = (status != 500)
    exact = (status == expect_status)
    b = body if isinstance(body, dict) else {}
    code_ok = error_code(b) == expect_code
    data_none = b.get("data") is None or b.get("data") == []
    no_alias = "error_code" not in b
    ok = not_500 and exact and code_ok and data_none and no_alias
    return ok, ("status=%s expect=%s not_500=%s exact=%s code=%r data_none=%s no_alias=%s"
                % (status, expect_status, not_500, exact, error_code(b), data_none, no_alias))


def judge_fingerprint_discriminative(fp_fn, obj_a, obj_b, inherited):
    """可复跑指纹判定：确定性 + 区分性 + 不等于继承恒量。"""
    f1, e1 = safe_call(fp_fn, copy.deepcopy(obj_a))
    f2, e2 = safe_call(fp_fn, copy.deepcopy(obj_a))
    f3, e3 = safe_call(fp_fn, copy.deepcopy(obj_b))
    deterministic = (e1 is None and e2 is None and f1 is not None and f1 == f2)
    discriminative = (e1 is None and e3 is None and f1 != f3)
    not_inherited = (f1 != inherited)
    ok = deterministic and discriminative and not_inherited
    return ok, ("deterministic=%s discriminative=%s not_inherited=%s f1=%r f3=%r"
                % (deterministic, discriminative, not_inherited, f1, f3))


# ══════════════════════════════════════════════════════════════════════════
# 5. L1 离线确定性层
# ══════════════════════════════════════════════════════════════════════════

def l1_module_load(p, mod, missing, err):
    ok = mod is not None and not missing and err is None
    p.check("L1-00 模块导入与必需导出齐全（9 项）", ok,
            ("ok, exports=%d" % len(REQUIRED_EXPORTS)) if ok
            else "IMPORT/EXPORT FAIL | missing=%s | err=%s" % (missing, err))
    p.evidence["module_source"] = str(resolve_module_path())
    p.evidence["required_exports"] = list(REQUIRED_EXPORTS)
    p.evidence["missing_exports"] = list(missing)
    p.evidence["import_error"] = err
    p.evidence["probe_evidence_error"] = EV_ERROR
    if ok:
        sigs = {}
        for n in REQUIRED_FUNCS:
            try:
                sigs[n] = str(inspect.signature(getattr(mod, n)))
            except Exception as e:
                sigs[n] = "ERR:%s" % e
        p.evidence["interface_variant"] = {
            "signatures": sigs,
            "upstream_error_class": upstream_error_class(mod).__name__,
            "note": "形态归一仅作用于字段抽取位置与调用方式，不改变判定标准",
        }
    return ok


def l1_blueprint_fingerprint(p, mod):
    def fake_envelope(data, ok=True, code=None, message=None):
        b = {"ok": ok, "data": data, "request_id": "fake", "server_time": "fake",
             "metric_fingerprint": INHERITED_FINGERPRINT}
        if code:
            b["error"] = {"code": code, "message": message or code}
        return b

    def fake_fail(code, message):
        return fake_envelope(None, ok=False, code=code, message=message)

    bp, variant = call_blueprint(mod, fake_envelope, fake_fail, T8094_PREFIX,
                                 dict(PLATFORM_ERROR_CODES))
    p.check("L1-01 create_blueprint(envelope, fail, api_prefix[, error_codes]) 可挂载",
            bp is not None, "type=%s factory_variant=%s" % (type(bp).__name__, variant))
    p.evidence["blueprint_type"] = type(bp).__name__ if bp is not None else None
    p.evidence["error_codes_factory"] = variant

    obj_a = {"applied_conds": ["cci_w>99"], "caveat": ["no_cost_model", "no_oos"], "n": 60}
    obj_b = dict(obj_a)
    obj_b["caveat"] = ["no_cost_model", "no_oos", "conditional_survival_sample"]
    f1, e1 = safe_call(mod.build_payload_fingerprint, copy.deepcopy(obj_a))
    f2, e2 = safe_call(mod.build_payload_fingerprint, copy.deepcopy(obj_a))
    f3, e3 = safe_call(mod.build_payload_fingerprint, copy.deepcopy(obj_b))
    f4, e4 = safe_call(mod.build_payload_fingerprint, {"applied_conds": ["cci_w>99"],
                                                        "caveat": ["no_cost_model"], "n": 60})
    p.check("L1-02 build_payload_fingerprint 确定性（同一 obj 两次相等）",
            e1 is None and e2 is None and f1 is not None and f1 == f2,
            "f1=%r f2=%r" % (f1, f2), assert_object="build_payload_fingerprint")
    p.check("L1-03 build_payload_fingerprint 区分性（不同 obj 必须不等，证伪指纹恒量）",
            e1 is None and e3 is None and f1 is not None and f3 is not None and f1 != f3,
            "f1=%r f3=%r changed=%s" % (f1, f3, f1 != f3),
            assert_object="build_payload_fingerprint")
    p.check("L1-04 改 caveat 后 build_payload_fingerprint 必须不等（data-engineer 反证）",
            e1 is None and e4 is None and f1 != f4,
            "base=%r caveat_changed=%r unequal=%s" % (f1, f4, f1 != f4),
            assert_object="build_payload_fingerprint")
    p.check("L1-05 build_payload_fingerprint 返回非空字符串",
            isinstance(f1, str) and len(f1) >= 8, "type=%s" % type(f1).__name__,
            assert_object="build_payload_fingerprint")
    p.evidence["fingerprint_samples"] = {"same_1": f1, "same_2": f2,
                                         "diff_obj": f3, "caveat_changed": f4}
    return f1


def l1_f2(p, mod, valid_conds):
    for c in INVALID_CONDS:
        spy = SpyUpstream(payload=FIX_PICK)
        raw, err = safe_call(mod.handle_pick, {"conds": c, "limit": 5}, spy)
        body, ec, variant = norm_result(raw)
        ok, detail = (False, "调用异常(明确判 FAIL, 不吞): %s" % err) if err is not None \
            else judge_invalid_conds(body)
        p.record("F2-invalid:%s" % (c or "<empty>"), body)
        p.check("L1-F2 无效 conds 显式失败 [%s]" % (c or "<空串>"), ok,
                detail + " | variant=%s" % variant)

    for c in EXTRA_INVALID_CONDS:
        spy = SpyUpstream(payload=FIX_PICK)
        raw, err = safe_call(mod.handle_pick, {"conds": c, "limit": 5}, spy)
        body, ec, variant = norm_result(raw)
        ok, detail = (False, "调用异常(明确判 FAIL, 不吞): %s" % err) if err is not None \
            else judge_invalid_conds(body)
        p.record("F2-extra:%s" % c, body)
        p.check("L1-F2 扩展无效 conds 必须显式失败 [%s]" % c, ok, detail)

    spy = SpyUpstream(payload=FIX_PICK)
    raw, err = safe_call(mod.handle_pick, {"conds": valid_conds, "limit": 5}, spy)
    body, ec, variant = norm_result(raw)
    vok, vdetail = (False, "调用异常(明确判 FAIL, 不吞): %s" % err) if err is not None \
        else judge_valid_conds(body, valid_conds)
    p.record("F2-valid:%s" % valid_conds, body)
    p.check("L1-F2 有效 conds 成功且 applied_conds 回显 [%s]" % valid_conds, vok, vdetail)

    # guard_conds 反例（lead 第 2 条）
    neg = []
    for c in GUARD_NEGATIVE:
        r, e = safe_call(mod.guard_conds, c)
        body, _, _ = norm_result(r)
        is_false = (e is None and (body.get("ok") is False if isinstance(body, dict) else r is False))
        neg.append({"conds": c, "ok_false": is_false, "err": e})
    p.check("L1-guard_conds 5 个反例全部 ok=False", all(x["ok_false"] for x in neg),
            json.dumps(neg, ensure_ascii=False, default=str),
            assert_object="guard_conds")

    # guard_conds 正例（lead 第 2 条）
    r, e = safe_call(mod.guard_conds, GUARD_POSITIVE)
    body, _, _ = norm_result(r)
    t = jtext(body)
    applied_all = e is None and isinstance(body, dict) and body.get("ok") is True \
        and ("applied" in t or "applied_conds" in t)
    discarded_empty = ('"discarded": []' in t or '"discarded":[]' in t or
                       "discarded" not in t)
    p.check("L1-guard_conds 正例全部 applied 且 discarded 为空", applied_all and discarded_empty,
            "conds=%r applied_all=%s discarded_empty=%s err=%s"
            % (GUARD_POSITIVE, applied_all, discarded_empty, e),
            assert_object="guard_conds")
    return body


def l1_f4(p, mod):
    r1, variant = call_stats_scope(mod, copy.deepcopy(DEFAULT_AGG), copy.deepcopy(FULL_AGG))
    r2, _ = call_stats_scope(mod, copy.deepcopy(DEFAULT_AGG), copy.deepcopy(FULL_AGG))
    p.evidence["stats_scope_signature_variant"] = variant
    scopes = collect_objects(r1, lambda d: "truncation_rule" in d or "sample_n" in d)
    four = ("sample_n", "window", "limit", "truncation_rule")
    p.check("L1-F4 stats_scope default/full 双 scope 均存在且各带 4 字段",
            r1 is not None and len(scopes) >= 2 and
            all(all(k in s for k in four) for s in scopes),
            "scopes=%d signature=%s" % (len(scopes), variant),
            assert_object="stats_scope")

    verdicts = [s.get("strategy_verdict") for s in scopes if s.get("strategy_verdict") is not None]
    if isinstance(r1, dict) and r1.get("strategy_verdict") is not None:
        verdicts.append(r1.get("strategy_verdict"))
    caveat = r1.get("caveat") if isinstance(r1, dict) else None
    caveat_hit = bool(caveat) and CAVEAT_EXPECTED.issubset(set(caveat if isinstance(caveat, list) else []))
    p.check("L1-F4 caveat 四枚举命中 且 strategy_verdict 恒 not_approved",
            bool(verdicts) and all(v == "not_approved" for v in verdicts) and caveat_hit,
            "verdicts=%s caveat=%r caveat_hit=%s" % (verdicts, caveat, caveat_hit),
            assert_object="stats_scope")

    # lead 第 6 条：两 scope agg 读数必须不同（相同判 FAIL，防恒真）
    def agg_numbers(scope):
        vals = []
        for node in collect_objects(scope, lambda d: any(k in d for k in ("win", "avg", "n"))):
            for k, v in node.items():
                if k in ("win", "avg", "n") and isinstance(v, (int, float)) and not isinstance(v, bool):
                    vals.append(round(float(v), 6))
        return sorted(set(vals))
    a_nums = [agg_numbers(s) for s in scopes[:2]]
    agg_diff = len(a_nums) == 2 and a_nums[0] != a_nums[1] and bool(a_nums[0]) and bool(a_nums[1])
    p.check("L1-F4 两 scope 的 agg 读数不同（相同判 FAIL，防恒真）", agg_diff,
            "scope_numeric_sets=%s | 输入真实读数 default=%s full=%s"
            % (a_nums, jtext(DEFAULT_AGG)[:150], jtext(FULL_AGG)[:150]),
            assert_object="stats_scope")

    det = diff_fields(r1, r2) if r1 is not None and r2 is not None else ["call error"]
    p.check("L1-F4 stats_scope 输出确定性（两次调用逐字段差异 0）", len(det) == 0,
            "diffs=%s" % det[:4], assert_object="stats_scope")
    p.evidence["f4_scopes"] = {
        "scope_count": len(scopes), "caveat": caveat,
        "winrate_equality_asserted": False,
        "note": "不对上游胜率数值做相等判定；仅校验结构/枚举/verdict/双口径差异/确定性。"
                "stats.total(5850) 与 signals.total(515) 同名异义，禁止互判；"
                "配对 n=413/412/407 与 agg.n 两套计数，禁止互校。",
    }
    return r1


def l1_f5(p, mod):
    """F5 按裁决 4-1：可复算、缺口是契约未披露 → 补披露；不写 UNVERIFIABLE。"""
    spy = SpyUpstream(payload=SIGNALS_FIX)
    raw, err = safe_call(mod.handle_signals, SIGNAL_CODE, {"group": "G1", "limit": 60}, spy)
    body, ec, variant = norm_result(raw)
    p.record("F5-signals", body)
    data_s = body.get("data") if isinstance(body, dict) else {}
    if not isinstance(data_s, dict):
        data_s = {}

    # lead 第 7 条原文为「stock/signals data」：若模块导出 handle_stock，一并回放。
    # handle_stock 走模块级 client（不消费 upstream 参数），故用 monkeypatch 注入，
    # try/finally 恢复，避免污染后续 L1/L2 的 fake upstream 注入。
    data_k = {}
    stock_variant = "not-exported"
    if callable(getattr(mod, "handle_stock", None)):
        orig_client = getattr(mod, "client", None)
        fake = CountingUpstream({"code": SIGNAL_CODE, "daily": [],
                                 "meta": {"universe_start": FWD.get("universe_start") or "2015-06-01"}})
        try:
            mod.client = fake
            rk, ek = safe_call(mod.handle_stock, SIGNAL_CODE, {"days": 60})
        finally:
            mod.client = orig_client
        bk, _, _ = norm_result(rk)
        p.record("F5-stock", bk)
        data_k = bk.get("data") if isinstance(bk, dict) else {}
        if not isinstance(data_k, dict):
            data_k = {}
        stock_variant = "ok" if ek is None else ("err:%s" % ek)

    datas = [("signals", data_s), ("stock", data_k)]
    formula_raw, formula_norm = None, ""
    for _src, d in datas:
        cand = d.get("fwd_formula") if isinstance(d, dict) else None
        if cand:
            formula_raw = cand
            formula_norm = re.sub(r"\s+", "", str(cand))
            if formula_norm == "close[t+h]/open[t+1]-1":
                break
    p.check("L1-F5 fwd_formula == 'close[t+h]/open[t+1]-1'（stock/signals 口径披露）",
            formula_norm == "close[t+h]/open[t+1]-1",
            "raw=%r normalized=%r sources=%s err=%s"
            % (formula_raw, formula_norm, [s for s, _ in datas], err),
            assert_object="data.fwd_formula")

    # lead 第 7 条四个披露字段：stock/signals 任一命中即可；语义等价归一 + 原值记录
    aliases = {
        "price_basis": ("price_basis", "fwd_price_basis", "basis"),
        "fwd_sample_n": ("fwd_sample_n", "fwd_n", "daily_bars", "sample_n"),
        "history_window": ("history_window", "daily_window", "window",
                           "daily_window_truncated", "daily_first"),
        "universe_start_mismatch": ("universe_start_mismatch", "window_inconsistent",
                                    "universe_start"),
    }
    found = {}
    for want, keys in aliases.items():
        hit = None
        for src, d in datas:
            for k in keys:
                if isinstance(d, dict) and k in d:
                    hit = ("%s.%s" % (src, k), d[k])
                    break
            if hit:
                break
        found[want] = hit
        p.check("L1-F5 stock/signals 含 %s 披露字段" % want, hit is not None,
                "found=%r | 归一候选=%s | sources=%s"
                % (hit, keys, [s for s, _ in datas]),
                assert_object="data." + want)

    # 真实读数自证可复算（来自 evidence.fwd_recompute，非编造）
    per = FWD.get("per_horizon", {})
    max_err = max((v.get("t1_open_max_abs_err") or 9.0) for v in per.values()) if per else 9.0
    c2c = min((v.get("close_to_close_mean_abs_err") or 0.0) for v in per.values()) if per else 0.0
    p.check("L1-F5 真实读数证明可复算：T+1 开盘口径最大绝对误差 ≤ 1e-4",
            bool(per) and max_err <= 1e-4,
            "verified_formula=%r max_abs_err=%s per_horizon=%s"
            % (FWD.get("verified_formula"), max_err, jtext(per)[:220]),
            assert_object="evidence.fwd_recompute")
    p.check("L1-F5 收盘到收盘口径误差显著更大（证明口径非它）", c2c > 0.005,
            "close_to_close_mean_err=%s" % c2c,
            assert_object="evidence.fwd_recompute")
    p.evidence["f5_disclosure"] = {
        "formula_raw": formula_raw, "found": found,
        "sources": {"signals_data_keys": sorted(data_s.keys()),
                    "stock_data_keys": sorted(data_k.keys())},
        "handle_stock": stock_variant,
        "daily_first": FWD.get("daily_first"), "daily_len": FWD.get("daily_len"),
        "universe_start": FWD.get("universe_start"),
        "window_inconsistent": FWD.get("daily_first") != FWD.get("universe_start"),
        "verified_formula": FWD.get("verified_formula"),
        "max_abs_err": max_err,
        "note": "裁决 4-1：F5 可复算，改造动作为补披露，不标 UNVERIFIABLE、不改算法。"
                "lead 第 7 条为 stock/signals data，故两个来源任一提供即可。",
    }
    return body


def l1_f3(p, mod):
    r, err = safe_call(mod.normalize_pick, copy.deepcopy(PICK_ITEMS))
    holder = r.get("data") if isinstance(r, dict) and isinstance(r.get("data"), dict) else r
    out_items = holder.get("items") if isinstance(holder, dict) and isinstance(holder.get("items"), list) else []
    filtered_count = holder.get("filtered_count") if isinstance(holder, dict) else None
    filter_rules = holder.get("filter_rules") if isinstance(holder, dict) else None
    null_n = sum(1 for x in out_items if isinstance(x, dict) and
                 any(x.get(k) is None for k in ("adx", "pdi", "mdi")))
    st_n = sum(1 for x in out_items if isinstance(x, dict) and "ST" in str(x.get("name", "")))
    sentinel_present = any(isinstance(x, dict) and x.get("code") == "sz000851" for x in PICK_ITEMS)
    sentinel_removed = not any(isinstance(x, dict) and x.get("code") == "sz000851" for x in out_items)
    removed = len(PICK_ITEMS) - len(out_items)
    p.check("L1-F3 normalize_pick 默认剔除 null/ST/无名：输出 null=0 且 ST=0",
            err is None and null_n == 0 and st_n == 0,
            "in=%d out=%d null=%d st=%d err=%s" % (len(PICK_ITEMS), len(out_items), null_n, st_n, err),
            assert_object="normalize_pick")
    p.check("L1-F3 filtered_count > 0 且等于真实剔除数",
            err is None and filtered_count == removed and removed > 0,
            "filtered_count=%r removed=%d" % (filtered_count, removed),
            assert_object="normalize_pick.filtered_count")
    rules_ok = isinstance(filter_rules, (list, tuple, set)) and len(filter_rules) > 0 \
        and set(filter_rules).issubset(FILTER_RULE_ENUM)
    p.check("L1-F3 filter_rules 为枚举子集", rules_ok, "filter_rules=%r" % (filter_rules,),
            assert_object="normalize_pick.filter_rules")
    p.check("L1-F3 伪零哨兵 sz000851（cci14=0.0/bias6=0.0）必须被排除",
            sentinel_present and sentinel_removed,
            "fixture_has_sz000851=%s removed=%s | 只按 is None 过滤会漏掉伪零"
            % (sentinel_present, sentinel_removed),
            assert_object="normalize_pick")
    # lead 第 5 条：include_nulls=True 不过滤（当前实现若无此能力 → 明确判 FAIL）
    r2, e2 = safe_call(mod.normalize_pick, copy.deepcopy(PICK_ITEMS), include_nulls=True)
    h2 = r2.get("data") if isinstance(r2, dict) and isinstance(r2.get("data"), dict) else r2
    n2 = len(h2.get("items", [])) if isinstance(h2, dict) else 0
    p.check("L1-F3 normalize_pick(include_nulls=True) 不过滤（保留全部）",
            e2 is None and n2 == len(PICK_ITEMS),
            "kept=%d expected=%d err=%s | 若函数不支持 include_nulls 则为契约能力缺失"
            % (n2, len(PICK_ITEMS), e2),
            assert_object="normalize_pick.include_nulls")
    p.evidence["f3_counts"] = {"in": len(PICK_ITEMS), "out": len(out_items), "removed": removed,
                               "filtered_count": filtered_count, "null_after": null_n,
                               "st_after": st_n, "filter_rules": filter_rules,
                               "sentinel_sz000851_present": sentinel_present,
                               "sentinel_sz000851_removed": sentinel_removed,
                               "include_nulls_supported": e2 is None}


def l1_degrade(p, mod, valid_conds):
    up_err = upstream_error_class(mod)
    spy = SpyUpstream(payload=FIX_PICK, raises=up_err("上游不可用（注入）"))
    raw, err = safe_call(mod.handle_pick, {"conds": valid_conds, "limit": 5}, spy)
    body, ec, variant = norm_result(raw)
    p.record("degrade:UpstreamError", body)
    if err is not None:
        ok_struct, detail = False, "调用异常(明确判 FAIL, 不吞): %s" % err
    else:
        ok_false = isinstance(body, dict) and body.get("ok") is False
        degraded = isinstance(body, dict) and body.get("degraded") is True
        code_ok = error_code(body) in ("UPSTREAM_UNAVAILABLE", "UPSTREAM_TIMEOUT",
                                       "UPSTREAM_CONTRACT_MISMATCH")
        prov = find_key(body, "provenance")
        stale = bool(prov) and bool(prov.get("stale"))
        disc, dd = disclaimer_ok(body)
        nonrt = NON_REALTIME in jtext(body)
        ok_struct = ok_false and degraded and code_ok and stale and disc and nonrt
        detail = ("ok_false=%s degraded=%s code=%r stale=%s disclaimer=%s 非实时=%s"
                  % (ok_false, degraded, error_code(body), stale, disc, nonrt))
    p.check("L1-降级 UpstreamError → UPSTREAM_UNAVAILABLE/degraded/stale/disclaimer/非实时",
            ok_struct, detail + " | upstream_error_class=%s" % up_err.__name__,
            assert_object="handle_pick(degraded)")

    data = body.get("data") if isinstance(body, dict) else "<no-resp>"
    hits = code_hits(data)
    data_empty = data is None or data == [] or data == {}
    item_n = len(data.get("items")) if isinstance(data, dict) and isinstance(data.get("items"), list) else 0
    p.check("L1-降级 data is None / 无样例选股 / 半包次数 0",
            err is None and data_empty and hits == 0 and item_n == 0,
            "data_type=%s code_hits=%d items=%d" % (type(data).__name__, hits, item_n),
            assert_object="handle_pick(data)")


def l1_boundary(p, mod, valid_conds):
    cases = [
        ("days=999999", [("handle_signals", lambda spy: mod.handle_signals(
            SIGNAL_CODE, {"group": "G1", "days": 999999}, spy))]),
        ("days=0", [("handle_signals", lambda spy: mod.handle_signals(
            SIGNAL_CODE, {"group": "G1", "days": 0}, spy))]),
        ("limit=999999", [("handle_pick", lambda spy: mod.handle_pick(
            {"conds": valid_conds, "limit": 999999}, spy))]),
        ("非法 code", [("handle_signals", lambda spy: mod.handle_signals(
            "NOTEXIST", {"group": "G1"}, spy))]),
        ("非法 group", [("handle_scan", lambda spy: mod.handle_scan(
            {"group": "NOT_A_GROUP", "limit": 5}, spy))]),
    ]
    summary = []
    for label, builders in cases:
        results = []
        for fname, fn in builders:
            spy = SpyUpstream(payload=FIX_PICK)
            raw, err = safe_call(fn, spy)
            body, ec, variant = norm_result(raw)
            rejected = (body.get("ok") is False) and \
                (error_code(body) in ("INVALID_ARGUMENT", "NOT_FOUND", "INVALID_CONDS")) \
                if isinstance(body, dict) else False
            no_upstream = len(spy.calls) == 0
            results.append({"fn": fname, "error_code": error_code(body),
                            "spy_calls": len(spy.calls), "rejected": rejected,
                            "no_upstream": no_upstream, "err": err})
            p.record("boundary:%s:%s" % (label, fname), body)
        ok = any(r["rejected"] and r["no_upstream"] for r in results)
        p.check("L1-边界 %s → 4xx 类错误码 且 fake upstream 调用计数 == 0" % label, ok,
                json.dumps(results, ensure_ascii=False, default=str),
                assert_object="upstream.call_count")
        summary.append({"case": label, "ok": ok, "results": results})
    p.evidence["boundary"] = summary


def l1_repro(p, mod, valid_conds, fp_sample):
    """可复跑：连跑 ≥10 次，payload_fingerprint 全等；禁止用继承恒量做相等断言。"""
    runs = []
    for _ in range(10):
        spy = SpyUpstream(payload=FIX_PICK)
        raw, err = safe_call(mod.handle_pick, {"conds": valid_conds, "limit": 5}, spy)
        body, ec, variant = norm_result(raw)
        runs.append((body, err, variant))
    bodies = [b for b, e, v in runs]
    errs = [e for b, e, v in runs]
    fps = [(find_key(b, "provenance") or {}).get("payload_fingerprint") for b in bodies]
    all_fp = all(f is not None for f in fps) and len(set(fps)) == 1
    p.check("L1-可复跑 同一 fixture 连跑 10 次 provenance.payload_fingerprint 全相等",
            all(e is None for e in errs) and all_fp,
            "runs=10 distinct_fps=%d fp=%r | 排除 {server_time,request_id}，"
            "metric_fingerprint 仅作继承自检、未用作相等判据" % (len(set(fps)), fps[0] if fps else None),
            assert_object="provenance.payload_fingerprint")

    diffs = diff_fields(bodies[0], bodies[-1], exclude=frozenset({"server_time", "request_id"}))
    p.check("L1-可复跑 排除 {server_time,request_id} 后逐字段差异数 = 0",
            all(e is None for e in errs) and len(diffs) == 0,
            "diffs=%s excluded=[server_time, request_id]" % diffs[:4],
            assert_object="envelope.fields")

    trap_ok = bool(fps) and fps[0] is not None and fps[0] != INHERITED_FINGERPRINT \
        and fps[0] != fp_sample
    p.check("L1-指纹陷阱规避 payload_fingerprint 不等于继承恒量", trap_ok,
            "payload_fp=%r inherited=%r build_sample=%r distinct=%s"
            % (fps[0] if fps else None, INHERITED_FINGERPRINT, fp_sample, trap_ok),
            assert_object="provenance.payload_fingerprint")

    inherited = {label: resp.get("metric_fingerprint") for label, resp in p.all_responses
                 if isinstance(resp, dict) and "metric_fingerprint" in resp}
    inh_ok = len(inherited) > 0 and all(v == INHERITED_FINGERPRINT for v in inherited.values())
    p.check("L1-继承能力自检 metric_fingerprint == 'e819989972c994d1'（零新增功能，仅验证复用）",
            inh_ok, "checked=%d values=%s" % (len(inherited), sorted(set(inherited.values()))),
            assert_object="metric_fingerprint")
    p.evidence["repro"] = {
        "runs": 10, "distinct_payload_fingerprints": len(set(fps)),
        "payload_fp": fps[0] if fps else None,
        "inherited_fingerprint": INHERITED_FINGERPRINT,
        "excluded_keys": ["server_time", "request_id"],
        "metric_fingerprint_used_as_repro_gate": False,
        "inherited_values": sorted(set(inherited.values())),
        "interface_variant": runs[0][2] if runs else None,
    }


def l1_f8(p):
    n, passed, banned_total = len(p.all_responses), 0, 0
    breakdown = {w: 0 for w in BANNED_WORDS}
    misses = []
    for label, resp in p.all_responses:
        ok, detail = disclaimer_ok(resp)
        bc = banned_count(resp)
        banned_total += bc
        t = jtext(resp)
        for w in BANNED_WORDS:
            breakdown[w] += t.count(w)
        if ok and bc == 0:
            passed += 1
        else:
            misses.append({"label": label, "disclaimer": detail, "banned": bc,
                           "hits": {w: t.count(w) for w in BANNED_WORDS if t.count(w)}})
    p.check("L1-F8 全量输出 disclaimer 完整率 100%（成功/错误/降级）",
            n > 0 and passed == n,
            "total=%d passed=%d rate=%s misses=%s"
            % (n, passed, ("%.1f%%" % (100.0 * passed / n)) if n else "n/a", misses[:3]))
    p.check("L1-F8 禁词「操盘/买卖点/目标价/收益承诺」全量字面计数 0",
            banned_total == 0, "banned_total=%d 逐词分布=%s（否定式列举同样计入）"
            % (banned_total, breakdown))
    p.evidence["f8_disclaimer_coverage"] = {
        "total": n, "passed": passed, "rate": (100.0 * passed / n) if n else None,
        "banned_total": banned_total, "banned_breakdown": breakdown,
        "misses": misses, "policy": "字面计数 0；否定式列举不豁免",
    }


def run_l1(p, mod, valid_conds):
    if mod is None:
        return
    fp = l1_blueprint_fingerprint(p, mod)
    l1_f2(p, mod, valid_conds)
    l1_f4(p, mod)
    l1_f5(p, mod)
    l1_f3(p, mod)
    l1_degrade(p, mod, VALID_CONDS)
    l1_boundary(p, mod, VALID_CONDS)
    l1_repro(p, mod, VALID_CONDS, fp)
    l1_f8(p)   # 必须最后：此时 all_responses 收齐成功/错误/降级全量输出


# ══════════════════════════════════════════════════════════════════════════
# 6. L2 在线契约层：自建独立 Flask app + test_client()（不 import server.py）
# ══════════════════════════════════════════════════════════════════════════

def http_get(base, path, timeout=3.0):
    url = base.rstrip("/") + path
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                body = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                body = raw.decode("utf-8", "replace")
            return r.status, body, len(raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, raw.decode("utf-8", "replace"), len(raw)
    except Exception as exc:  # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, exc), 0


def build_l2_app(mod, drop_invalid_conds_code=False):
    """自建等价 app：PLATFORM_ERROR_CODES 故意不预注册规整层新码。
    drop_invalid_conds_code=True 用于反向对照：模拟「错误码未注册 → 500」。
    """
    from flask import Flask, jsonify

    codes = dict(PLATFORM_ERROR_CODES)

    def envelope(data, ok=True, code=None, message=None):
        b = {"ok": ok, "data": data, "request_id": "l2-" + code.__str__() if code else "l2",
             "server_time": "l2", "metric_fingerprint": INHERITED_FINGERPRINT}
        if code:
            b["error"] = {"code": code, "message": message or code}
        return jsonify(b), (200 if ok else codes.get(code, 500))

    def fail(code, message):
        return envelope(None, ok=False, code=code, message=message)

    app = Flask(__name__)

    @app.get(API_PREFIX + "/health")
    def _health():
        return envelope({"status": "ok", "service": "astock-quant-platform"})

    bp, variant = call_blueprint(mod, envelope, fail, T8094_PREFIX, codes)
    if bp is None:
        return app, None, variant
    app.register_blueprint(bp)

    # 反向对照专用：让「未注册新码」真实落 500（模块内映射路径）
    if drop_invalid_conds_code and hasattr(mod, "_STATUS_BY_CODE"):
        orig = dict(mod._STATUS_BY_CODE)
        mod._STATUS_BY_CODE = {k: v for k, v in orig.items() if k != "INVALID_CONDS"}
        app._restore_status_map = orig  # type: ignore[attr-defined]
    return app, app.test_client(), variant


L2_NAMES_EXTERNAL = (
    "L2-09 GET /terminal8094/meta → 200 + 信封最小集 + disclaimer.research_only",
    "L2-10 平台既有 /quant/api/v1/health → 200（继承能力自检）",
    "L2-11 在线 days=999999 → 4xx 且 != 500 且响应体 < 1MB",
)


def run_l2_external(p, base, strict, evidence_sink=None):
    """外网在线层（--base / --upstream-base）：不可达默认 SKIPPED，strict 时判 FAIL。"""
    pre, _, _ = http_get(base, API_PREFIX + "/health", timeout=3.0)
    req_log = []
    if pre is None:
        for name in L2_NAMES_EXTERNAL:
            p.check(name, False,
                    ("反向对照：L2 上游 %s 不可达，strict 判 FAIL" % base) if strict
                    else ("L2 在线层不可达，按裁决标记 SKIPPED —— 不判 PASS/FAIL: %s" % base),
                    layer="L2", status=None if strict else "SKIPPED")
        state = "FAILED" if strict else "SKIPPED"
        if evidence_sink is not None:
            evidence_sink["l2_external"] = {"base": base, "state": state, "requests": req_log}
        return state

    s, body, n = http_get(base, T8094_PREFIX + "/meta", timeout=5.0)
    req_log.append({"path": T8094_PREFIX + "/meta", "status": s, "bytes": n})
    min_set = {"ok", "data", "request_id", "server_time"}
    present = min_set.issubset(set(body)) if isinstance(body, dict) else False
    disc, dd = disclaimer_ok(body)
    p.check(L2_NAMES_EXTERNAL[0], s == 200 and present and disc,
            "status=%s min_set=%s disclaimer=%s(%s)" % (s, present, disc, dd), layer="L2")

    s2, body2, n2 = http_get(base, API_PREFIX + "/health", timeout=5.0)
    req_log.append({"path": API_PREFIX + "/health", "status": s2, "bytes": n2})
    p.check(L2_NAMES_EXTERNAL[1],
            s2 == 200 and isinstance(body2, dict) and body2.get("ok") is True,
            "status=%s ok=%r" % (s2, body2.get("ok") if isinstance(body2, dict) else None),
            layer="L2")

    candidates = [T8094_PREFIX + "/signals/" + SIGNAL_CODE + "?days=999999",
                  T8094_PREFIX + "/stock/" + SIGNAL_CODE + "?days=999999",
                  T8094_PREFIX + "/pick?days=999999"]
    detail = []
    for path in candidates:
        st, _, by = http_get(base, path, timeout=6.0)
        req_log.append({"path": path, "status": st, "bytes": by})
        detail.append({"path": path, "status": st, "bytes": by})
    any_4xx_not500 = any(isinstance(x["status"], int) and 400 <= x["status"] < 500
                         and x["status"] != 500 for x in detail)
    all_small = all(x["bytes"] < 1024 * 1024 for x in detail)
    p.check(L2_NAMES_EXTERNAL[2], any_4xx_not500 and all_small,
            "any_4xx_and_not_500=%s all_lt_1MB=%s detail=%s" % (any_4xx_not500, all_small, detail),
            layer="L2")
    if evidence_sink is not None:
        evidence_sink["l2_external"] = {"base": base, "state": "RAN", "requests": req_log,
                                        "forbidden_probe": T8094_PREFIX + "/health 未探测"}
    return "RAN"


def run_l2_inprocess(p, mod, evidence_sink=None):
    """主 L2：自建 app + test_client，计数 fake upstream 注入，离线确定性。"""
    if mod is None:
        for n in ("L2-01 INVALID_CONDS 400 且 !=500", "L2-03 成功 /pick 信封",
                  "L2-08 边界 4xx 且 !=500 且 upstream 计数 0",
                  "L2-09 降级 502 且 !=500", "L2-反500 显式门禁",
                  "L2-平台 /health 继承自检", "L2-meta 挂载证明"):
            p.check(n, False, "模块不可用 → L2 明确判 FAIL，不静默跳过", layer="L2")
        return "FAILED"

    orig_client = getattr(mod, "client", None)
    app, tc, variant = build_l2_app(mod)
    if tc is None:
        p.check("L2-00 自建 Flask app + create_blueprint + test_client 可用", False,
                "build failed: %s" % variant, layer="L2")
        return "FAILED"
    p.check("L2-00 自建 Flask app + create_blueprint + test_client 可用", True,
            "app=%s factory_variant=%s（不 import apps/api/server.py）"
            % (type(app).__name__, variant), layer="L2")

    statuses = {}

    # ---- 缺陷注入用 upstream ----
    class Silent500(Exception):
        pass

    try:
        # 1) INVALID_CONDS（含第一条 lead 的在线 pick?conds=<无效>）
        payload = FIX_PICK
        fake = CountingUpstream(payload)
        mod.client = fake
        r = tc.get(T8094_PREFIX + "/pick?conds=1%2F0")
        body = r.get_json(silent=True)
        ok, detail = judge_http_invalid_conds(r.status_code, body)
        statuses["invalid_conds"] = r.status_code
        p.check("L2-01 INVALID_CONDS → status 400 且 !=500；ok=false/error.code/data is None/"
                "无 sz000851/无顶层 error_code 别名", ok, detail, layer="L2",
                assert_object="http.status + error.code")

        # 1b) 在线 days=999999 → 4xx 且 != 500（第一条 lead）
        fake2 = CountingUpstream(payload)
        mod.client = fake2
        r2 = tc.get(T8094_PREFIX + "/pick?days=999999")
        b2 = r2.get_json(silent=True)
        ok2, d2 = judge_http_error(r2.status_code, b2, 400, "INVALID_ARGUMENT")
        statuses["days_999999"] = r2.status_code
        p.check("L2-01b 在线 days=999999 → 4xx 且 !=500（反 500 静默通过）", ok2,
                d2 + " | upstream_calls=%d（边界应在调用上游之前拦截）" % len(fake2.calls),
                layer="L2", assert_object="http.status")
        p.check("L2-08 days=999999 边界校验在调用上游之前（fake upstream 计数 == 0）",
                len(fake2.calls) == 0, "upstream_calls=%d" % len(fake2.calls),
                layer="L2", assert_object="upstream.call_count")

        # 3) 成功 /pick 信封
        fake3 = CountingUpstream(payload)
        mod.client = fake3
        r3 = tc.get(T8094_PREFIX + "/pick?conds=cci_w%3E99")
        b3 = r3.get_json(silent=True) or {}
        statuses["pick_ok"] = r3.status_code
        p.check("L2-03 成功 /pick 信封 ok=true / request_id 非空 / server_time 非空 / "
                "metric_fingerprint == e819989972c994d1（仅继承能力自检）",
                r3.status_code == 200 and b3.get("ok") is True
                and bool(b3.get("request_id")) and bool(b3.get("server_time"))
                and b3.get("metric_fingerprint") == INHERITED_FINGERPRINT,
                "status=%s request_id=%r server_time=%r mf=%r"
                % (r3.status_code, b3.get("request_id"), b3.get("server_time"),
                   b3.get("metric_fingerprint")),
                layer="L2", assert_object="metric_fingerprint")

        # 9) 降级：fake upstream 抛上游异常 → 502 且 !=500
        up_err = upstream_error_class(mod)
        fake4 = CountingUpstream(payload, raises=up_err("上游不可用（L2 注入）"))
        mod.client = fake4
        r4 = tc.get(T8094_PREFIX + "/pick?conds=cci_w%3E99")
        b4 = r4.get_json(silent=True) or {}
        statuses["degrade"] = r4.status_code
        not_500 = r4.status_code != 500
        degraded_ok = (b4.get("degraded") is True and b4.get("data") is None
                       and error_code(b4) in ("UPSTREAM_UNAVAILABLE", "UPSTREAM_TIMEOUT",
                                             "UPSTREAM_CONTRACT_MISMATCH")
                       and code_hits(b4.get("data")) == 0)
        p.check("L2-09 降级 → 502 且 !=500 / UPSTREAM_UNAVAILABLE / data is None / "
                "degraded=true / 无半包", r4.status_code == 502 and not_500 and degraded_ok,
                "status=%s not_500=%s code=%r degraded=%r data=%r"
                % (r4.status_code, not_500, error_code(b4), b4.get("degraded"), b4.get("data")),
                layer="L2", assert_object="http.status + degraded")

        # 反 500 显式门禁（汇总）
        err_statuses = {k: v for k, v in statuses.items() if k != "pick_ok"}
        all_not_500 = all(v != 500 for v in err_statuses.values())
        p.check("L2-反500 显式门禁：所有错误响应 status != 500（把静默 500 变成可检测失败）",
                all_not_500, "error_statuses=%s all_not_500=%s" % (err_statuses, all_not_500),
                layer="L2", assert_object="http.status != 500")

        # 平台 /health 继承自检
        rh = tc.get(API_PREFIX + "/health")
        bh = rh.get_json(silent=True) or {}
        p.check("L2-10 平台既有 /quant/api/v1/health → 200（继承能力自检）",
                rh.status_code == 200 and bh.get("ok") is True
                and bh.get("metric_fingerprint") == INHERITED_FINGERPRINT,
                "status=%s mf=%r" % (rh.status_code, bh.get("metric_fingerprint")),
                layer="L2", assert_object="metric_fingerprint")

        # meta 挂载证明（绝不探 /terminal8094/health）
        fake5 = CountingUpstream({"meta": {"universe_start": FWD.get("universe_start")}})
        mod.client = fake5
        rm = tc.get(T8094_PREFIX + "/meta")
        bm = rm.get_json(silent=True) or {}
        min_set = {"ok", "data", "request_id", "server_time"}
        disc, dd = disclaimer_ok(bm)
        p.check("L2-02 GET /terminal8094/meta → 200 + 信封最小集 + disclaimer.research_only",
                rm.status_code == 200 and min_set.issubset(set(bm)) and disc,
                "status=%s min_set=%s disclaimer=%s(%s) | 未探测 /terminal8094/health"
                % (rm.status_code, min_set.issubset(set(bm)), disc, dd),
                layer="L2", assert_object="disclaimer.research_only")

        if evidence_sink is not None:
            evidence_sink["l2_inprocess"] = {
                "upstream_mode": "fixture-replay", "factory_variant": variant,
                "statuses": statuses,
                "fake_upstream_calls": {"invalid_conds": len(fake.calls),
                                        "days_999999": len(fake2.calls),
                                        "pick_ok": len(fake3.calls),
                                        "degrade": len(fake4.calls)},
                "forbidden_probe": T8094_PREFIX + "/health 未探测",
            }
        return "RAN"
    finally:
        mod.client = orig_client


def run_l2_live(p, evidence_sink=None):
    """live 冒烟：3s 内上游可达则跑；不可达不判 FAIL。"""
    s, body, n = http_get(UPSTREAM_LIVE, "/api/meta", timeout=3.0)
    reachable = (s == 200 and isinstance(body, dict))
    if not reachable:
        p.check("L2-live 上游 :8094 冒烟（不可达，按裁决不判 FAIL）", True,
                "status=%s base=%s → live 冒烟跳过，upstream_mode=fixture-replay"
                % (s, UPSTREAM_LIVE), layer="L2")
        if evidence_sink is not None:
            evidence_sink["l2_live"] = {"reachable": False, "status": s,
                                        "upstream_mode": "fixture-replay"}
        return "fixture-replay"
    n_stocks = 0
    try:
        n_stocks = int((body.get("meta") or body).get("n_stocks", 0))
    except Exception:
        pass
    p.check("L2-live 上游 :8094 /api/meta 200 且 n_stocks > 0（只读 GET）",
            n_stocks > 0, "status=%s n_stocks=%s bytes=%d" % (s, n_stocks, n), layer="L2")
    if evidence_sink is not None:
        evidence_sink["l2_live"] = {"reachable": True, "status": s,
                                    "upstream_mode": "live", "n_stocks": n_stocks}
    return "live"


# ══════════════════════════════════════════════════════════════════════════
# 7. 反向对照（成对：正常 → PASS / 注入 → 必须 FAIL）
# ══════════════════════════════════════════════════════════════════════════

def unused_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def negative_controls(p, mod, valid_conds, l2_external_base, extra_defects=False):
    # ── NC1 L2 上游不可达 ──
    dead = "http://127.0.0.1:%d" % unused_port()
    shadow = Probe()
    state = run_l2_external(shadow, dead, strict=True)
    nc1 = (state == "FAILED" and shadow.failed == len(L2_NAMES_EXTERNAL) and shadow.passed == 0)
    p.nc("NC1 L2 上游不可达（成对：可达→PASS / 未监听端口→L2 判 FAIL）", nc1,
         "dead_base=%s state=%s failed=%d/%d | 外部复现 --upstream-base <死端口> → 退出码 1"
         % (dead, state, shadow.failed, len(L2_NAMES_EXTERNAL)))
    p.evidence["nc1"] = {"dead_base": dead, "state": state, "failed": shadow.failed}

    # ── NC2 fixture 篡改/缺失（同一 judge_invalid_conds 复判） ──
    clean = {"ok": False, "data": None, "error": {"code": "INVALID_CONDS"},
             "disclaimer": {"research_only": True, "text": "仅供研究，不构成投资建议"},
             "metric_fingerprint": INHERITED_FINGERPRINT}
    tampered = {"ok": True, "data": {"items": [
        {"code": "sz000851", "name": "*ST高鸿", "close": 0.38}], "total": 1},
        "request_id": "tampered", "metric_fingerprint": INHERITED_FINGERPRINT}
    c_ok, c_d = judge_invalid_conds(clean)
    t_ok, t_d = judge_invalid_conds(tampered)
    # NC2b：篡改 fwd 真实读数 → 可复算判据必须 FAIL
    real_max = FWD.get("per_horizon", {}).get("20", {}).get("t1_open_max_abs_err") or 9.0
    clean_pass = real_max <= 1e-4
    tampered_max = real_max + 0.05
    tampered_rejected = tampered_max > 1e-4
    nc2 = c_ok and (not t_ok) and clean_pass and tampered_rejected
    p.nc("NC2 fixture 篡改/缺失（成对：合规→通过 / 篡改默认清单+fwd→判定 FAIL）", nc2,
         "NC2a judge_invalid_conds: clean=%s tampered=%s | NC2b 可复算: real_max=%.8f(通过=%s) "
         "tampered_max=%.8f(判FAIL=%s) | 结论：两类 L1 断言确实在检测"
         % (c_ok, t_ok, real_max, clean_pass, tampered_max, tampered_rejected))
    p.evidence["nc2"] = {"clean_ok": c_ok, "tampered_ok": t_ok,
                         "real_max": real_max, "tampered_max": tampered_max}

    # ── NC3 恒量冒充可复跑判据（证明用恒量做相等是恒真检查） ──
    const_a = {"data": {"items": [1, 2, 3]}, "metric_fingerprint": INHERITED_FINGERPRINT,
               "request_id": "a", "server_time": "a"}
    const_b = {"data": {"items": [9, 9, 9]}, "metric_fingerprint": INHERITED_FINGERPRINT,
               "request_id": "b", "server_time": "b"}
    trivially_equal = (const_a["metric_fingerprint"] == const_b["metric_fingerprint"])
    real_diff = diff_fields(const_a["data"], const_b["data"])
    nc3 = trivially_equal and len(real_diff) > 0
    p.nc("NC3 恒量做可复跑相等判据是恒真检查（成对：data 已变 / 指纹恒量仍相等）", nc3,
         "两份 data 差异数=%d（真实不同），但 metric_fingerprint 恒量相等=%s → "
         "若拿恒量做可复跑断言会误判 PASS；故可复跑由 payload_fingerprint 承担"
         % (len(real_diff), trivially_equal))
    p.evidence["nc3"] = {"data_diff_count": len(real_diff),
                         "trivial_fingerprint_equal": trivially_equal}

    # ── NC4 三缺陷注入（lead --negative-control）──
    defects = []
    # 缺陷 1：错误码未注册 → 500（其余字段全部合规，唯独 status=500）
    d1_body = {"ok": False, "data": None, "error": {"code": "INVALID_CONDS"},
               "disclaimer": {"research_only": True, "text": "不构成投资建议"}}
    d1_ok, d1_detail = judge_http_invalid_conds(500, d1_body)
    defects.append(("错误码未注册→status=500（其余字段全合规）", not d1_ok, d1_detail))

    # 缺陷 1b：真实链路注入，确认能造出 500 再复判
    real_500 = None
    if mod is not None and hasattr(mod, "_STATUS_BY_CODE"):
        orig = dict(mod._STATUS_BY_CODE)
        orig_client = getattr(mod, "client", None)
        try:
            mod._STATUS_BY_CODE = {k: v for k, v in orig.items() if k != "INVALID_CONDS"}
            app2, tc2, _ = build_l2_app(mod)
            mod.client = CountingUpstream(FIX_PICK)
            if tc2 is not None:
                rr = tc2.get(T8094_PREFIX + "/pick?conds=1%2F0")
                real_500 = rr.status_code
                rb = rr.get_json(silent=True)
                ok_500, _ = judge_http_invalid_conds(rr.status_code, rb)
                defects[0] = (defects[0][0], defects[0][1] and (not ok_500),
                              "shadow=%s | 真实链路 status=%s 复判通过?=%s"
                              % (d1_detail[:90], rr.status_code, ok_500))
        finally:
            mod._STATUS_BY_CODE = orig
            mod.client = orig_client
    p.evidence["nc4_real_500_status"] = real_500

    # 缺陷 2：conds 非法却返回默认清单
    d2_ok, d2_detail = judge_invalid_conds(tampered)
    defects.append(("conds 非法却返回默认选股清单（sz000851）", not d2_ok, d2_detail))

    # 缺陷 3：payload_fingerprint 用 metric_fingerprint 恒量代替
    def const_fp(_obj):
        return INHERITED_FINGERPRINT
    d3_ok, d3_detail = judge_fingerprint_discriminative(
        const_fp, {"a": 1}, {"a": 2}, INHERITED_FINGERPRINT)
    defects.append(("payload_fingerprint 用 metric_fingerprint 恒量代替", not d3_ok, d3_detail))

    for i, (label, detected, detail) in enumerate(defects, 1):
        p.nc("NC4-%d %s" % (i, label), bool(detected),
             "注入缺陷后同一判定函数必须 FAIL | observed_fail=%s | %s" % (detected, detail[:260]),
             expected_fail=True, observed_fail=bool(detected))
        if extra_defects:
            # --negative-control：把注入的缺陷也落到主 checks，让其成为**可见的 FAIL**，
            # 开关因此有真实语义（退出码 1），而不只是改一个 evidence 标记。
            p.check("[NC4-INJECT] 缺陷%d：%s → 校验器必须报 FAIL" % (i, label),
                    False, "预期 FAIL（注入缺陷），observed_fail=%s | %s"
                    % (detected, detail[:220]), layer="NC4", status="FAIL")
    p.evidence["nc4_defects"] = [{"defect": l, "observed_fail": d, "detail": dt}
                                 for l, d, dt in defects]
    if extra_defects:
        p.evidence["negative_control_mode"] = {
            "enabled": True, "defects_injected": 3,
            "all_detected": all(d for _, d, _ in defects),
            "note": "shadow probe 自检（negative_controls[]）+ 主 checks 可见的 3 条预期 FAIL；"
                    "layer=NC4 不污染 L1/L2 统计，但触发退出码 1",
        }
    else:
        p.evidence["negative_control_mode"] = {"enabled": False,
                                               "note": "三缺陷 shadow 自检默认每次都跑，不污染主 checks"}


# ══════════════════════════════════════════════════════════════════════════
# 8. main
# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=":8094 规整层双层验收校验器（只读 GET，0 写入上游）")
    ap.add_argument("--base", default=DEFAULT_BASE, help="外网在线层 base")
    ap.add_argument("--upstream-base", default=None,
                    help="反向对照 A：覆盖 L2 目标并进入 strict，未监听端口必须判 FAIL、退出码 1")
    ap.add_argument("--self-test-negative", action="store_true",
                    help="反向对照 C：把有效 conds 替换为无效 conds，F2 断言组必须出现 FAIL")
    ap.add_argument("--negative-control", action="store_true",
                    help="三缺陷注入自检（shadow probe，不改主流程结果）")
    args = ap.parse_args()

    self_neg = bool(args.self_test_negative)
    valid_conds = INVALID_CONDS[-1] if self_neg else VALID_CONDS  # 精准注入点
    l2_base = args.upstream_base or args.base
    l2_strict = args.upstream_base is not None

    p = Probe()
    mod, missing, err = load_module()
    load_ok = l1_module_load(p, mod, missing, err)

    if load_ok:
        run_l1(p, mod, valid_conds)
    else:
        p.check("L1 其余确定性检查（模块不可用 → 全部判 FAIL，不静默跳过）", False,
                "IMPORT/EXPORT FAIL: missing=%s err=%s" % (missing, err))
        p.check("L1-F8 全量输出 disclaimer 完整率 100%（模块不可用）", False,
                "模块不可用，无法采集全量输出 → 明确判 FAIL")

    inprocess_state = run_l2_inprocess(p, mod, evidence_sink=p.evidence)
    external_state = run_l2_external(p, l2_base, l2_strict, evidence_sink=p.evidence)
    live_mode = run_l2_live(p, evidence_sink=p.evidence)
    negative_controls(p, mod, valid_conds, l2_base, extra_defects=args.negative_control)

    if self_neg and p.failed == 0:
        p.check("SELF-TEST-NEGATIVE 自检失败：注入无效 conds 后 F2 断言组未出现 FAIL（疑似恒真）",
                False, "注入 %r 后 failed=0" % valid_conds)
    p.evidence["self_test_negative"] = {"enabled": self_neg, "injected_valid_conds": valid_conds,
                                        "failed_checks_detected": p.failed if self_neg else None}

    l1 = p.layer_stat("L1")
    l2 = p.layer_stat("L2")
    l2["upstream_mode"] = live_mode
    l2["inprocess"] = inprocess_state
    l2["external"] = external_state
    nc_ok = len(p.negative_controls) > 0 and all(n["ok"] for n in p.negative_controls)
    overall = (p.total > 0) and (p.failed == 0) and nc_ok and (l1["ok"] is True)

    gaps = []
    if p.evidence.get("error_codes_factory", "").startswith("3-arg"):
        gaps.append("create_blueprint 缺 error_codes 工厂参数（当前用模块内 _STATUS_BY_CODE 决定状态码）")
    if not p.evidence.get("f3_counts", {}).get("include_nulls_supported"):
        gaps.append("normalize_pick 缺 include_nulls=True 能力")
    f5 = p.evidence.get("f5_disclosure", {}) or {}
    for want, hit in (f5.get("found") or {}).items():
        if hit is None:
            gaps.append("F5 披露字段 %s 未提供" % want)
        elif not str(hit[0]).endswith("." + want):
            gaps.append("F5 披露字段 %s 以别名 %s 提供（已按别名归一通过）"
                        % (want, hit[0]))
    formula_raw = f5.get("formula_raw")
    if formula_raw is not None and \
            re.sub(r"\s+", "", str(formula_raw)) != "close[t+h]/open[t+1]-1":
        gaps.append("F5 fwd_formula 口径不匹配（当前=%r）" % formula_raw)
    p.evidence["contract_gaps"] = [g for g in gaps if g]
    p.evidence["note"] = (
        "L1 断言输出 dict 字段、不判 HTTP 状态码；L2 自建 Flask app + test_client 判 HTTP 状态码"
        "且显式 status != 500；禁止探 /quant/api/v1/terminal8094/health；"
        "metric_fingerprint 为继承恒量，仅作继承自检、未用作可复跑相等判据；"
        "fixture 由 output/terminal_8094_probe.json evidence 真实读数构造；"
        "不对上游胜率数值做相等判定；全程只读 GET，0 写入上游。"
    )

    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    payload = {
        "ok": bool(overall), "passed": p.passed, "total": p.total,
        "failed": p.failed, "skipped": p.skipped,
        "negative_controls_passed": sum(1 for n in p.negative_controls if n["ok"]),
        "negative_controls_total": len(p.negative_controls),
        "layers": {"l1": l1, "l2": l2, "L1": l1, "L2": l2},
        "checks": p.checks, "negative_controls": p.negative_controls,
        "evidence": p.evidence,
        "contract_advisories": p.evidence["contract_gaps"],
        "base": args.base, "target": "apps/api/terminal8094.py 规整层 · Phase 3 验收",
        "method": "L1 import+fixture 回放（必跑） / L2 自建 Flask app+test_client（fixture-replay）+ 只读 GET 在线冒烟",
        "check_time": now,
        "params": {"base": args.base, "upstream_base": args.upstream_base,
                   "self_test_negative": self_neg, "negative_control": args.negative_control,
                   "l2_base": l2_base, "l2_strict": l2_strict, "module": str(resolve_module_path())},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    label = "PASS" if overall else "FAIL"
    print("TERMINAL_8094_ADAPTER: %s (%d/%d)" % (label, p.passed, p.total))
    print("TERMINAL8094_ADAPTER_CHECK: %s (%d/%d, failed=%d, skipped=%d, nc=%d/%d, L2=%s/%s)"
          % (label, p.passed, p.total, p.failed, p.skipped,
             payload["negative_controls_passed"], payload["negative_controls_total"],
             inprocess_state, external_state))
    for c in p.checks:
        if c["status"] == "FAIL":
            print("  MISS: %s | %s" % (c["name"], c["detail"][:220]))
    for n in p.negative_controls:
        if not n["ok"]:
            print("  NC-MISS: %s | %s" % (n["name"], n["detail"][:200]))
    print("EVIDENCE: %s" % OUT)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
