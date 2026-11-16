"""研究状态持久化：预注册假设、策略参数指纹、样本外使用次数。

针对专家团审核 followup #5：v2 的 oos_use_count 只是 config 里的静态常量，
任何人回改参数都不会留痕。本模块把「策略参数指纹」写进 state/research_state.json，
指纹变化即视为一次基于（或影响）样本外的回改 → 计数 +1；超过 oos_max_uses
由质量门自动降级为不放行。

纪律：
  * 指纹只覆盖影响策略信号/交易成本的段（leverage/cost/factors/strategy/risk/period），
    validation 与 data 段不进指纹，避免「加一个检验维度」被误记成调参；
  * 预注册假设一旦写入即冻结，禁止静默覆盖（防 HARKing）；
  * 状态文件纳入版本控制，历史逐条留痕，不删除、不回退。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "research_state.json"
HASH_SECTIONS = ("leverage", "cost", "factors", "strategy", "risk", "period")

DEFAULT_HYPOTHESIS = (
    "6 个价量因子经横截面 rank-Z 标准化后按预注册权重线性合成为正向 alpha；"
    "叠加 MA60/MA120 个股趋势过滤、MA200 等权样本指数择时、回撤熔断与月末调仓，"
    "在 2018-01-01~2026-11-13 的 A 股样本上可获得显著优于随机选股的风险调整收益。"
    "样本外自 2024-01-01 起，原则上仅使用一次；方向重估须作为新假设在全新样本重走流程。"
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strategy_hash(cfg) -> str:
    """策略参数指纹：只 hash 影响信号与成本的配置段，排序后规范化 JSON。"""
    payload = {k: cfg.section(k) for k in HASH_SECTIONS}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha(blob)


def _default_state(cfg, hypothesis: str | None = None) -> dict:
    h = hypothesis or DEFAULT_HYPOTHESIS
    return {
        "schema_version": "1.0",
        "created_at": _now(),
        "preregistration": {
            "hypothesis": h,
            "hash": _sha(h),
            "registered_at": _now(),
            "frozen": True,
            "note": "预注册一旦写入即冻结；修改须显式 --force 并同时递增样本外使用次数",
        },
        "strategy_hash": strategy_hash(cfg),
        "oos_use_count": 1,
        "history": [],
    }


def load_state(path: str | Path | None = None) -> dict | None:
    p = Path(path) if path else STATE_PATH
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, path: str | Path | None = None) -> Path:
    p = Path(path) if path else STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return p


def register(root: str | Path, cfg, hypothesis: str, force: bool = False) -> dict:
    """写入预注册假设。已存在且未 force 时拒绝覆盖（防 HARKing）。"""
    p = Path(root) / "state" / "research_state.json"
    st = load_state(p)
    if st is None:
        st = _default_state(cfg, hypothesis)
        save_state(st, p)
        return {"ok": True, "action": "created", "path": str(p),
                "hypothesis_hash": st["preregistration"]["hash"]}
    if not force:
        return {"ok": False, "action": "frozen", "path": str(p),
                "hypothesis_hash": st["preregistration"]["hash"],
                "message": "预注册已冻结；确需改动请加 --force（会递增样本外使用次数）"}
    old = st["preregistration"]["hash"]
    st["preregistration"] = {"hypothesis": hypothesis, "hash": _sha(hypothesis),
                             "registered_at": _now(), "frozen": True,
                             "supersedes": old, "forced": True}
    st["oos_use_count"] = int(st.get("oos_use_count", 1)) + 1
    st.setdefault("history", []).append({
        "at": _now(), "event": "preregistration_force_changed",
        "old_hash": old, "new_hash": _sha(hypothesis),
        "oos_use_count": st["oos_use_count"],
    })
    save_state(st, p)
    return {"ok": True, "action": "forced", "path": str(p),
            "hypothesis_hash": _sha(hypothesis), "oos_use_count": st["oos_use_count"]}


def sync_run(root: str | Path, cfg) -> dict:
    """每次研究运行前同步状态；返回供 config 注入与质量门消费的读数。"""
    p = Path(root) / "state" / "research_state.json"
    st = load_state(p)
    h = strategy_hash(cfg)
    if st is None:
        st = _default_state(cfg)
        save_state(st, p)
        event = "baseline_initialized"
    elif h != st.get("strategy_hash"):
        old = st.get("strategy_hash")
        st["oos_use_count"] = int(st.get("oos_use_count", 1)) + 1
        st["strategy_hash"] = h
        st.setdefault("history", []).append({
            "at": _now(), "event": "strategy_params_changed",
            "old_strategy_hash": old, "new_strategy_hash": h,
            "oos_use_count": st["oos_use_count"],
            "interpretation": "策略参数或方向发生回改，视为一次样本外使用",
        })
        save_state(st, p)
        event = "strategy_params_changed"
    else:
        event = "unchanged"
    max_uses = int(cfg.get("validation.oos_max_uses", 3))
    count = int(st["oos_use_count"])
    return {
        "path": str(p),
        "event": event,
        "strategy_hash": h,
        "oos_use_count": count,
        "oos_max_uses": max_uses,
        "degraded": count > max_uses,
        "preregistration_hash": st.get("preregistration", {}).get("hash"),
        "hypothesis": st.get("preregistration", {}).get("hypothesis"),
        "history_len": len(st.get("history", [])),
        "note": "指纹变化即计一次样本外使用；超过上限由质量门硬否决",
    }
